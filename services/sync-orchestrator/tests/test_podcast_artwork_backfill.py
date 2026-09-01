from iopenpod.podcasts.models import PodcastEpisode, PodcastFeed

from sync_orchestrator import podcast_artwork_backfill as backfill_module
from sync_orchestrator.podcast_artwork_backfill import build_podcast_artwork_backfill_items


def _episode(
    guid="ep-1",
    *,
    title="Episode One",
    audio_url="https://cdn.example/ep1.mp3",
    downloaded_path="/library/podcasts/Test Show/Episode One.mp3",
) -> PodcastEpisode:
    return PodcastEpisode(
        guid=guid,
        title=title,
        audio_url=audio_url,
        downloaded_path=downloaded_path,
    )


def _feed(episodes, *, title="Test Show", feed_url="podcast-manager:show-1") -> PodcastFeed:
    return PodcastFeed(feed_url=feed_url, title=title, episodes=list(episodes))


def _ipod_podcast_track(
    *,
    db_track_id=42,
    enclosure_url="https://cdn.example/ep1.mp3",
    title="Episode One",
    album="Test Show",
    artwork_count=0,
    artwork_id_ref=0,
):
    return {
        "media_type": 0x04,
        "db_track_id": db_track_id,
        "Podcast Enclosure URL": enclosure_url,
        "Title": title,
        "Album": album,
        "artwork_count": artwork_count,
        "artwork_id_ref": artwork_id_ref,
    }


def _stub_art(monkeypatch, *, art_bytes=b"fake-jpeg-bytes", hash_value="deadbeef"):
    monkeypatch.setattr(
        backfill_module, "extract_art_with_folder", lambda path: art_bytes
    )
    monkeypatch.setattr(backfill_module, "art_hash", lambda data: hash_value)


def test_artless_on_device_episode_with_folder_art_is_backfilled(monkeypatch):
    _stub_art(monkeypatch, hash_value="abc123")
    feeds = [_feed([_episode()])]
    ipod_tracks = [_ipod_podcast_track(artwork_count=0, artwork_id_ref=0)]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert len(items) == 1
    assert items[0].db_track_id == 42
    assert items[0].new_art_hash == "abc123"
    assert items[0].old_art_hash is None
    assert matched_pc_paths == {42: "/library/podcasts/Test Show/Episode One.mp3"}


def test_episode_already_showing_artwork_count_is_left_alone(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode()])]
    ipod_tracks = [_ipod_podcast_track(artwork_count=1)]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert items == []
    assert matched_pc_paths == {}


def test_episode_already_showing_artwork_id_ref_is_left_alone(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode()])]
    ipod_tracks = [_ipod_podcast_track(artwork_count=0, artwork_id_ref=7)]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert items == []


def test_episode_not_yet_on_device_is_left_for_build_podcast_sync_plan(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode()])]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, [])

    assert items == []
    assert matched_pc_paths == {}


def test_episode_without_downloaded_path_is_skipped(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode(downloaded_path="")])]
    ipod_tracks = [_ipod_podcast_track()]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert items == []


def test_no_folder_or_embedded_art_available_produces_no_item(monkeypatch):
    _stub_art(monkeypatch, art_bytes=None)
    feeds = [_feed([_episode()])]
    ipod_tracks = [_ipod_podcast_track()]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert items == []
    assert matched_pc_paths == {}


def test_matches_by_title_and_album_when_enclosure_url_absent_from_device_track(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode(title="Episode One")], title="Test Show")]
    track = _ipod_podcast_track(enclosure_url="")
    track["Title"] = "Episode One"
    track["Album"] = "Test Show"

    items, _ = build_podcast_artwork_backfill_items(feeds, [track])

    assert len(items) == 1


def test_non_podcast_tracks_are_ignored_for_matching(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode(title="Episode One")], title="Test Show")]
    music_track = {
        "media_type": 0x01,
        "db_track_id": 7,
        "Title": "Episode One",
        "Album": "Test Show",
        "artwork_count": 0,
    }

    items, _ = build_podcast_artwork_backfill_items(feeds, [music_track])

    assert items == []


def test_multiple_artless_episodes_each_produce_their_own_item(monkeypatch):
    _stub_art(monkeypatch, hash_value="samehash")
    feeds = [
        _feed(
            [
                _episode("ep-1", title="Episode One", audio_url="https://cdn.example/ep1.mp3",
                          downloaded_path="/library/podcasts/Test Show/Episode One.mp3"),
                _episode("ep-2", title="Episode Two", audio_url="https://cdn.example/ep2.mp3",
                          downloaded_path="/library/podcasts/Test Show/Episode Two.mp3"),
            ]
        )
    ]
    ipod_tracks = [
        _ipod_podcast_track(db_track_id=1, enclosure_url="https://cdn.example/ep1.mp3", title="Episode One"),
        _ipod_podcast_track(db_track_id=2, enclosure_url="https://cdn.example/ep2.mp3", title="Episode Two"),
    ]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, ipod_tracks)

    assert {item.db_track_id for item in items} == {1, 2}
    assert set(matched_pc_paths) == {1, 2}


def test_track_missing_db_track_id_is_skipped(monkeypatch):
    _stub_art(monkeypatch)
    feeds = [_feed([_episode()])]
    track = _ipod_podcast_track()
    del track["db_track_id"]

    items, matched_pc_paths = build_podcast_artwork_backfill_items(feeds, [track])

    assert items == []
    assert matched_pc_paths == {}
