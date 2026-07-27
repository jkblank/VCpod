from common.state import EpisodeRecord
from sync_orchestrator.podcast_removal import build_podcast_removal_items


def _episode(
    episode_uuid="ep-1",
    *,
    show_name="Test Show",
    title="Episode One",
    audio_url="https://cdn.example/ep1.mp3",
    played=True,
) -> EpisodeRecord:
    return EpisodeRecord(
        episode_uuid=episode_uuid,
        podcast_uuid="show-1",
        show_name=show_name,
        local_path=f"/library/podcasts/{show_name}/{title}.mp3",
        played=played,
        played_up_to=0,
        downloaded_at="2026-07-19T00:00:00+00:00",
        title=title,
        audio_url=audio_url,
    )


def _ipod_podcast_track(
    *,
    db_track_id=42,
    enclosure_url="https://cdn.example/ep1.mp3",
    title="Episode One",
    album="Test Show",
    size=12345,
):
    return {
        "media_type": 0x04,
        "db_track_id": db_track_id,
        "Podcast Enclosure URL": enclosure_url,
        "Title": title,
        "Album": album,
        "size": size,
    }


def test_played_episode_on_device_is_proposed_for_removal():
    episodes = [_episode(played=True)]
    ipod_tracks = [_ipod_podcast_track()]

    items = build_podcast_removal_items(episodes, ipod_tracks)

    assert len(items) == 1
    assert items[0].db_track_id == 42
    assert items[0].ipod_track["size"] == 12345


def test_unplayed_episode_is_not_proposed_for_removal():
    episodes = [_episode(played=False)]
    ipod_tracks = [_ipod_podcast_track()]

    items = build_podcast_removal_items(episodes, ipod_tracks)

    assert items == []


def test_played_episode_not_on_device_is_not_proposed():
    # Played, but this device's iTunesDB has no matching track — e.g.
    # never synced to this particular iPod. Nothing to remove.
    episodes = [_episode(played=True)]
    ipod_tracks = []

    items = build_podcast_removal_items(episodes, ipod_tracks)

    assert items == []


def test_matches_by_title_and_album_when_enclosure_url_absent_from_device_track():
    episodes = [_episode(played=True, title="Episode One", show_name="Test Show")]
    track = _ipod_podcast_track(enclosure_url="")
    track["Title"] = "Episode One"
    track["Album"] = "Test Show"

    items = build_podcast_removal_items(episodes, [track])

    assert len(items) == 1


def test_title_album_match_is_case_insensitive():
    episodes = [_episode(played=True, title="Episode One", show_name="Test Show")]
    track = _ipod_podcast_track(enclosure_url="")
    track["Title"] = "EPISODE ONE"
    track["Album"] = "test show"

    items = build_podcast_removal_items(episodes, [track])

    assert len(items) == 1


def test_non_podcast_tracks_are_ignored_for_matching():
    # A music track that happens to share a title/album with a podcast
    # episode must never be proposed for removal by this function.
    episodes = [_episode(played=True, title="Episode One", show_name="Test Show")]
    music_track = {
        "media_type": 0x01,  # not the podcast flag
        "db_track_id": 7,
        "Title": "Episode One",
        "Album": "Test Show",
        "size": 999,
    }

    items = build_podcast_removal_items(episodes, [music_track])

    assert items == []


def test_multiple_played_episodes_each_produce_their_own_removal_item():
    episodes = [
        _episode("ep-1", title="Episode One", audio_url="https://cdn.example/ep1.mp3"),
        _episode("ep-2", title="Episode Two", audio_url="https://cdn.example/ep2.mp3"),
    ]
    ipod_tracks = [
        _ipod_podcast_track(db_track_id=1, enclosure_url="https://cdn.example/ep1.mp3", title="Episode One"),
        _ipod_podcast_track(db_track_id=2, enclosure_url="https://cdn.example/ep2.mp3", title="Episode Two"),
    ]

    items = build_podcast_removal_items(episodes, ipod_tracks)

    assert {item.db_track_id for item in items} == {1, 2}


def test_bytes_removed_are_recoverable_from_ipod_track_size():
    # sync.py sums item.ipod_track["size"] into plan.storage.bytes_to_remove
    # — confirm the field this depends on is actually populated.
    episodes = [_episode(played=True)]
    ipod_tracks = [_ipod_podcast_track(size=999_000)]

    items = build_podcast_removal_items(episodes, ipod_tracks)

    assert items[0].ipod_track["size"] == 999_000
