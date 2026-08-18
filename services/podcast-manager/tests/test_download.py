import shutil
from pathlib import Path

import httpx
import pytest

from common.lock import FileLock, LockTimeoutError
from common.state import EpisodeRecord, StateDB
from podcast_manager import download as download_module
from podcast_manager.api import EpisodeState, FullEpisode, PodcastSummary

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FIXTURE_AUDIO = (FIXTURES / "episode.mp3").read_bytes()

PODCAST = PodcastSummary(uuid="show-1", title="Test Show", author="Author")

FULL_EPISODES = [
    FullEpisode(
        uuid="ep-0",
        title="Newest Episode",
        url="https://cdn.example/ep0.mp3",
        published="2026-03-01T00:00:00Z",
        duration=100,
    ),
    FullEpisode(
        uuid="ep-1",
        title="Middle Episode",
        url="https://cdn.example/ep1.mp3",
        published="2026-02-01T00:00:00Z",
        duration=100,
    ),
    FullEpisode(
        uuid="ep-2",
        title="Oldest Episode",
        url="https://cdn.example/ep2.mp3",
        published="2026-01-01T00:00:00Z",
        duration=100,
    ),
]


class FakeStreamResponse:
    def raise_for_status(self):
        pass

    def iter_bytes(self):
        yield FIXTURE_AUDIO

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


@pytest.fixture
def patched_pipeline(monkeypatch):
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: FULL_EPISODES)
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kwargs: FakeStreamResponse()
    )
    # Retries use real time.sleep() backoff — not wanted in tests.
    monkeypatch.setattr(download_module.time, "sleep", lambda seconds: None)
    # No feed resolved by default -- tests that care about RSS enrichment
    # override this themselves. Without it, resolve_feed_url/
    # fetch_rss_episodes make real network calls against the iTunes
    # Search API / a real feed on every test run (confirmed live: this
    # slowed the whole suite from under a second to ~100s).
    monkeypatch.setattr(download_module, "resolve_feed_url", lambda title, author: None)


def _fetch(**overrides):
    kwargs = dict(
        podcast=PODCAST,
        token="tok",
        sync_unplayed_only=True,
        max_episodes_per_show=5,
    )
    kwargs.update(overrides)
    return download_module.sync_podcast(**kwargs)


def test_sync_podcast_downloads_unplayed_capped_at_max(monkeypatch, patched_pipeline, tmp_path):
    # ep-0 (newest) already played; sync_unplayed_only should exclude it.
    states = [EpisodeState(uuid="ep-0", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    assert len(result.downloaded) == 1
    assert result.downloaded[0].episode_uuid == "ep-1"  # newest unplayed


def test_sync_podcast_next_fill_mode_picks_oldest_unplayed(monkeypatch, patched_pipeline, tmp_path):
    # ep-0 (newest) already played; fill_mode="next" should pick the
    # OLDEST remaining unplayed episode (ep-2), not the newest (ep-1,
    # which is what "newest" mode — the default — would pick instead).
    states = [EpisodeState(uuid="ep-0", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
        fill_mode="next",
    )

    assert len(result.downloaded) == 1
    assert result.downloaded[0].episode_uuid == "ep-2"  # oldest unplayed


def test_sync_podcast_includes_played_when_not_unplayed_only(monkeypatch, patched_pipeline, tmp_path):
    states = [EpisodeState(uuid="ep-0", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        sync_unplayed_only=False,
        max_episodes_per_show=10,
    )

    assert len(result.downloaded) == 3


def test_sync_podcast_local_path_is_absolute_even_with_relative_library_root(
    monkeypatch, patched_pipeline, tmp_path
):
    # Confirmed live: a relative library_root produced a relative
    # local_path recorded in the state db, which sync-orchestrator's
    # _load_podcast_feeds() silently failed to re-resolve correctly
    # later (joining it onto its own already-absolute library_root
    # produced a wrong, doubled path) — 11 of 12 real subscribed shows'
    # episodes were missing from every real device sync as a result.
    # Same bug class already fixed in fetcher-apple/fetcher-ytmusic.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    monkeypatch.chdir(tmp_path)
    (tmp_path / "library").mkdir()

    result = _fetch(
        library_root=Path("library"),  # relative, matching a real invocation
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    assert len(result.downloaded) == 1
    assert Path(result.downloaded[0].local_path).is_absolute()


def test_sync_podcast_episode_filter_archived_excludes_archived_not_played(
    monkeypatch, patched_pipeline, tmp_path
):
    # ep-0 is archived but was never played — episode_filter="archived"
    # should exclude it on that basis alone, unlike the default "played"
    # filter which would have kept it (not played -> not excluded).
    states = [EpisodeState(uuid="ep-0", played=False, played_up_to=0, archived=True)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=10,
        episode_filter="archived",
    )

    assert {r.episode_uuid for r in result.downloaded} == {"ep-1", "ep-2"}


def test_sync_podcast_episode_filter_archived_includes_played_not_archived(
    monkeypatch, patched_pipeline, tmp_path
):
    # ep-0 is played but NOT archived — episode_filter="archived" should
    # still include it, unlike the default "played" filter which would
    # have excluded it.
    states = [EpisodeState(uuid="ep-0", played=True, played_up_to=100, archived=False)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=10,
        episode_filter="archived",
    )

    assert {r.episode_uuid for r in result.downloaded} == {"ep-0", "ep-1", "ep-2"}


def test_sync_podcast_excludes_episode_played_locally_but_not_on_pocket_casts(
    monkeypatch, patched_pipeline, tmp_path
):
    # Regression test for the "already-listened episodes get downloaded
    # anyway" bug (notes.md): Pocket Casts' own EpisodeState has no row
    # at all for ep-0 (as if never interacted with there), but M8's
    # device read-back already recorded it played locally. The local
    # signal must still exclude it from re-download.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    state_db_path = tmp_path / "state.sqlite"
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-0",
                podcast_uuid="show-1",
                show_name="Test Show",
                local_path=str(tmp_path / "library" / "Test Show" / "existing.mp3"),
                played=True,
                played_up_to=100,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=5,
    )

    downloaded_uuids = {r.episode_uuid for r in result.downloaded}
    assert "ep-0" not in downloaded_uuids


def test_sync_podcast_does_not_downgrade_locally_played_episode(
    monkeypatch, patched_pipeline, tmp_path
):
    # A subsequent sync (e.g. re-downloading other episodes for the same
    # show) must not silently reset an already-known-played episode back
    # to unplayed just because Pocket Casts' own state hasn't caught up
    # yet (or never will, for a listen Pocket Casts never saw). Uses
    # sync_unplayed_only=False so ep-0 is a re-sync candidate at all.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    state_db_path = tmp_path / "state.sqlite"
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-0",
                podcast_uuid="show-1",
                show_name="Test Show",
                local_path=str(tmp_path / "library" / "Test Show" / "placeholder.mp3"),
                played=True,
                played_up_to=100,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )

    _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        sync_unplayed_only=False,
        max_episodes_per_show=10,
    )

    with StateDB(state_db_path) as db:
        assert db.get_episode("ep-0").played is True


def _record_existing_episode(state_db_path, *, episode_uuid, local_path, played, podcast_uuid="show-1"):
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid=episode_uuid,
                podcast_uuid=podcast_uuid,
                show_name="Test Show",
                local_path=str(local_path),
                played=played,
                played_up_to=0,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )


def test_sync_podcast_refreshes_remote_played_state_for_non_candidate_episode(
    monkeypatch, patched_pipeline, tmp_path
):
    # Regression test for the "stale played state" gap: an episode played
    # only via the Pocket Casts app (not through the device) must have its
    # state-db row updated even though sync_unplayed_only excludes it from
    # this run's download candidates below.
    states = [EpisodeState(uuid="ep-1", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Middle Episode [ep-1].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-1", local_path=existing_path, played=False
    )

    _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        delete_played_episodes=False,
        max_episodes_per_show=1,
    )

    with StateDB(state_db_path) as db:
        refreshed = db.get_episode("ep-1")
        assert refreshed.played is True
        assert refreshed.played_up_to == 100


def test_sync_podcast_deletes_file_for_played_episode(monkeypatch, patched_pipeline, tmp_path):
    states = [EpisodeState(uuid="ep-1", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Middle Episode [ep-1].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-1", local_path=existing_path, played=False
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=1,
    )

    assert not existing_path.exists()
    assert [r.episode_uuid for r in result.deleted] == ["ep-1"]


def test_sync_podcast_prunes_episode_aged_out_of_window_even_if_unplayed(
    monkeypatch, patched_pipeline, tmp_path
):
    # Confirmed live, 2026-08-18: candidates[:max_episodes_per_show] only
    # ever capped new *additions* -- an already-downloaded, still-unplayed
    # episode that ages out of the top-N window because newer ones
    # arrived was never pruned, so shows built up well past their
    # configured limit (17 local episodes against a limit of 5, in one
    # real case). ep-2 is the oldest of the 3 FULL_EPISODES and is NOT
    # played -- with max_episodes_per_show=2, it falls out of the
    # (ep-0, ep-1) window and must be pruned even though unplayed.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Oldest Episode [ep-2].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-2", local_path=existing_path, played=False
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=2,
    )

    assert not existing_path.exists()
    assert "ep-2" in {r.episode_uuid for r in result.deleted}
    assert {r.episode_uuid for r in result.downloaded} == {"ep-0", "ep-1"}


def test_sync_podcast_does_not_prune_aged_out_episode_when_sync_unplayed_only_false(
    monkeypatch, patched_pipeline, tmp_path
):
    # sync_unplayed_only=False means the profile deliberately wants
    # everything kept (e.g. an archive) -- the cap-pruning must respect
    # the exact same gate the existing played-based pruning already does.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Oldest Episode [ep-2].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-2", local_path=existing_path, played=False
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=2,
        sync_unplayed_only=False,
    )

    assert existing_path.exists()
    assert result.deleted == []


def test_sync_podcast_attaches_rss_metadata_matched_by_enclosure_url(
    monkeypatch, patched_pipeline, tmp_path
):
    from podcast_manager.rss import RssEpisodeMeta

    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    monkeypatch.setattr(
        download_module, "resolve_feed_url", lambda title, author: "https://example.com/feed.xml"
    )
    monkeypatch.setattr(
        download_module,
        "fetch_rss_episodes",
        lambda feed_url: [
            RssEpisodeMeta(
                enclosure_url="https://cdn.example/ep0.mp3",  # matches FULL_EPISODES[0].url
                title="Newest Episode",
                description="Real show notes.",
                episode_number=7,
                season_number=2,
                published="Sun, 01 Mar 2026 00:00:00 -0000",
            )
        ],
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    record = result.downloaded[0]
    assert record.episode_uuid == "ep-0"
    assert record.description == "Real show notes."
    assert record.episode_number == 7
    assert record.season_number == 2
    assert record.published_at == "Sun, 01 Mar 2026 00:00:00 -0000"


def test_sync_podcast_falls_back_to_pocket_casts_published_when_rss_unresolved(
    monkeypatch, patched_pipeline, tmp_path
):
    # resolve_feed_url returning None (patched_pipeline's default) must
    # not leave published_at blank -- Pocket Casts' own `published` field
    # is a real, always-available fallback for that one field specifically.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    record = result.downloaded[0]
    assert record.description == ""
    assert record.episode_number is None
    assert record.published_at == "2026-03-01T00:00:00Z"  # FULL_EPISODES[0].published


def test_sync_podcast_keeps_file_when_delete_played_episodes_disabled(
    monkeypatch, patched_pipeline, tmp_path
):
    states = [EpisodeState(uuid="ep-1", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Middle Episode [ep-1].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-1", local_path=existing_path, played=False
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=1,
        delete_played_episodes=False,
    )

    assert existing_path.exists()
    assert result.deleted == []


def test_sync_podcast_keeps_played_files_when_sync_unplayed_only_false(
    monkeypatch, patched_pipeline, tmp_path
):
    # sync_unplayed_only=False means the profile deliberately wants played
    # episodes downloaded/kept too (e.g. an archive) — delete_played_episodes
    # must not fight that, even though it defaults to True.
    states = [EpisodeState(uuid="ep-1", played=True, played_up_to=100)]
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: states)

    state_db_path = tmp_path / "state.sqlite"
    existing_path = tmp_path / "library" / "Test Show" / "Middle Episode [ep-1].mp3"
    existing_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    _record_existing_episode(
        state_db_path, episode_uuid="ep-1", local_path=existing_path, played=False
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=10,
        sync_unplayed_only=False,
    )

    assert existing_path.exists()
    assert result.deleted == []


def test_sync_podcast_deletion_scoped_to_this_podcast_only(
    monkeypatch, patched_pipeline, tmp_path
):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    state_db_path = tmp_path / "state.sqlite"
    other_show_path = tmp_path / "library" / "Other Show" / "episode.mp3"
    other_show_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", other_show_path)
    _record_existing_episode(
        state_db_path,
        episode_uuid="other-ep",
        local_path=other_show_path,
        played=True,
        podcast_uuid="show-999",
    )

    _fetch(
        library_root=tmp_path / "library",
        state_db_path=state_db_path,
        max_episodes_per_show=1,
    )

    assert other_show_path.exists()


def test_sync_podcast_orders_newest_first_regardless_of_input_order(monkeypatch, tmp_path):
    shuffled = [FULL_EPISODES[2], FULL_EPISODES[0], FULL_EPISODES[1]]
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: shuffled)
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    monkeypatch.setattr(download_module, "resolve_feed_url", lambda title, author: None)
    monkeypatch.setattr(
        httpx, "stream", lambda method, url, **kwargs: FakeStreamResponse()
    )

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        sync_unplayed_only=False,
        max_episodes_per_show=1,
    )

    assert result.downloaded[0].episode_uuid == "ep-0"  # newest by published date


def test_sync_podcast_untouched_episode_has_no_state_row_and_is_treated_unplayed(
    monkeypatch, patched_pipeline, tmp_path
):
    # Confirms real behavior found live: episodes never interacted with have
    # NO row at all from list_episode_states — must still be treated as
    # unplayed, not crash or get excluded.
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    assert len(result.downloaded) == 1
    assert result.downloaded[0].played is False
    assert result.downloaded[0].played_up_to == 0


def test_sync_podcast_writes_correct_state_db_row(monkeypatch, patched_pipeline, tmp_path):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    state_db_path = tmp_path / "state.sqlite"

    result = _fetch(
        library_root=tmp_path / "library", state_db_path=state_db_path, max_episodes_per_show=1
    )

    record = result.downloaded[0]
    assert Path(record.local_path).exists()
    assert record.show_name == "Test Show"
    assert record.podcast_uuid == "show-1"

    with StateDB(state_db_path) as db:
        assert db.get_episode(record.episode_uuid) == record


def test_sync_podcast_skips_download_for_existing_shared_file(monkeypatch, tmp_path):
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: FULL_EPISODES[:1])
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    monkeypatch.setattr(download_module, "resolve_feed_url", lambda title, author: None)

    library_root = tmp_path / "library"
    show_dir = library_root / "Test Show"
    show_dir.mkdir(parents=True)
    existing_path = show_dir / "Newest Episode [ep-0].mp3"
    shutil.copy(FIXTURES / "episode.mp3", existing_path)
    original_mtime = existing_path.stat().st_mtime

    def _fail_stream(*args, **kwargs):
        raise AssertionError("should not re-download an already-present shared file")

    monkeypatch.setattr(httpx, "stream", _fail_stream)

    result = _fetch(
        library_root=library_root,
        state_db_path=tmp_path / "state.sqlite",
        sync_unplayed_only=False,
        max_episodes_per_show=10,
    )

    assert len(result.downloaded) == 0
    assert len(result.already_present) == 1
    assert existing_path.stat().st_mtime == original_mtime  # untouched

    with StateDB(tmp_path / "state.sqlite") as db:
        assert db.get_episode("ep-0") is not None


def test_sync_podcast_populates_title_audio_url_and_duration(monkeypatch, patched_pipeline, tmp_path):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    record = result.downloaded[0]
    assert record.title == "Newest Episode"
    assert record.audio_url == "https://cdn.example/ep0.mp3"
    assert record.duration_seconds == 100


def test_sync_podcast_backfills_metadata_without_redownloading_existing_file(
    monkeypatch, tmp_path
):
    # A pre-existing file with no local state-db record at all (e.g.
    # downloaded by another profile sharing this episode) must not be
    # redownloaded — but the record we write should still carry full
    # title/audio_url/duration metadata from the fresh API response.
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: FULL_EPISODES[:1])
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    library_root = tmp_path / "library"
    show_dir = library_root / "Test Show"
    show_dir.mkdir(parents=True)
    existing_path = show_dir / "Newest Episode [ep-0].mp3"
    shutil.copy(FIXTURES / "episode.mp3", existing_path)

    def _fail_stream(*args, **kwargs):
        raise AssertionError("should not re-download an already-present shared file")

    monkeypatch.setattr(httpx, "stream", _fail_stream)

    result = _fetch(
        library_root=library_root,
        state_db_path=tmp_path / "state.sqlite",
        sync_unplayed_only=False,
        max_episodes_per_show=10,
    )

    assert len(result.already_present) == 1
    record = result.already_present[0]
    assert record.title == "Newest Episode"
    assert record.audio_url == "https://cdn.example/ep0.mp3"
    assert record.duration_seconds == 100


def test_sync_podcast_one_episode_download_failure_does_not_abort_others(
    monkeypatch, tmp_path
):
    # Confirms real behavior found live: one episode's connection dropping
    # mid-download (RemoteProtocolError / ReadTimeout) must not prevent the
    # rest of the show's episodes from downloading. ep-1 fails on every
    # attempt here, so this also exercises _download_enclosure's retry
    # loop exhausting all attempts before finally giving up.
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: FULL_EPISODES)
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    monkeypatch.setattr(download_module, "resolve_feed_url", lambda title, author: None)
    monkeypatch.setattr(download_module.time, "sleep", lambda seconds: None)

    def _stream(method, url, **kwargs):
        if url == "https://cdn.example/ep1.mp3":
            raise httpx.ReadTimeout("simulated drop mid-download")
        return FakeStreamResponse()

    monkeypatch.setattr(httpx, "stream", _stream)

    result = _fetch(
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        sync_unplayed_only=False,
        max_episodes_per_show=10,
    )

    assert len(result.downloaded) == 2  # ep-0 and ep-2
    assert {r.episode_uuid for r in result.downloaded} == {"ep-0", "ep-2"}
    assert len(result.failed) == 1
    failed_episode, error = result.failed[0]
    assert failed_episode.uuid == "ep-1"
    assert "simulated drop" in error

    with StateDB(tmp_path / "state.sqlite") as db:
        # The failed episode must not get a state-db row — nothing was
        # actually downloaded for it, so there's no local_path to record.
        assert db.get_episode("ep-1") is None
        assert db.get_episode("ep-0") is not None


def test_download_enclosure_retries_and_succeeds_on_later_attempt(monkeypatch, tmp_path):
    # Confirmed live (2026-07-19): 6 episode downloads across 3 unrelated
    # CDN hosts failed with transient ReadTimeout/RemoteProtocolError
    # errors in one sync run — a few retries with backoff should clear
    # most of these without needing a whole extra `podcast-manager sync`
    # invocation.
    monkeypatch.setattr(download_module.time, "sleep", lambda seconds: None)

    attempts = {"count": 0}

    def _stream(method, url, **kwargs):
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise httpx.ReadTimeout("simulated transient drop")
        return FakeStreamResponse()

    monkeypatch.setattr(httpx, "stream", _stream)

    dest = tmp_path / "episode.mp3"
    download_module._download_enclosure("https://cdn.example/ep.mp3", dest)

    assert attempts["count"] == 2
    assert dest.is_file()
    assert dest.read_bytes() == FIXTURE_AUDIO
    assert not dest.with_suffix(dest.suffix + ".part").exists()


def test_download_enclosure_raises_after_exhausting_all_retries(monkeypatch, tmp_path):
    monkeypatch.setattr(download_module.time, "sleep", lambda seconds: None)

    attempts = {"count": 0}

    def _stream(method, url, **kwargs):
        attempts["count"] += 1
        raise httpx.ReadTimeout("persistent failure")

    monkeypatch.setattr(httpx, "stream", _stream)

    dest = tmp_path / "episode.mp3"
    with pytest.raises(httpx.ReadTimeout):
        download_module._download_enclosure("https://cdn.example/ep.mp3", dest)

    assert attempts["count"] == download_module._DOWNLOAD_RETRIES
    assert not dest.exists()


# --- sync_shows ---------------------------------------------------------------

PODCAST_2 = PodcastSummary(uuid="show-2", title="Second Show", author="Author Two")


def test_sync_shows_syncs_every_subscription(monkeypatch, patched_pipeline, tmp_path):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    outcomes = download_module.sync_shows(
        [PODCAST, PODCAST_2],
        token="tok",
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    assert [o.podcast.uuid for o in outcomes] == ["show-1", "show-2"]
    assert all(o.error is None for o in outcomes)
    assert all(len(o.result.downloaded) == 1 for o in outcomes)


def test_sync_shows_one_show_api_failure_does_not_abort_the_rest(
    monkeypatch, patched_pipeline, tmp_path
):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    real_list_full_episodes = download_module.list_full_episodes

    def _flaky_list_full_episodes(token, uuid):
        if uuid == "show-1":
            raise httpx.ReadTimeout("timed out")
        return real_list_full_episodes(token, uuid)

    monkeypatch.setattr(download_module, "list_full_episodes", _flaky_list_full_episodes)

    outcomes = download_module.sync_shows(
        [PODCAST, PODCAST_2],
        token="tok",
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
    )

    by_uuid = {o.podcast.uuid: o for o in outcomes}
    assert by_uuid["show-1"].error is not None
    assert by_uuid["show-1"].result is None
    assert by_uuid["show-2"].error is None
    assert len(by_uuid["show-2"].result.downloaded) == 1


def test_sync_shows_uses_per_show_fill_mode(monkeypatch, patched_pipeline, tmp_path):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    outcomes = download_module.sync_shows(
        [PODCAST],
        token="tok",
        library_root=tmp_path / "library",
        state_db_path=tmp_path / "state.sqlite",
        max_episodes_per_show=1,
        fill_modes={"show-1": "next"},
    )

    assert outcomes[0].result.downloaded[0].episode_uuid == "ep-2"  # oldest, per "next" fill mode


# --- Podcast sync lock -------------------------------------------------------


def test_sync_podcast_raises_lock_timeout_when_another_session_active(
    monkeypatch, patched_pipeline, tmp_path
):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    lock_path = tmp_path / ".podcasts.lock"

    holder = FileLock(lock_path, timeout=5)
    holder.acquire()
    try:
        with pytest.raises(LockTimeoutError):
            _fetch(
                library_root=tmp_path / "library",
                state_db_path=tmp_path / "state.sqlite",
                lock_path=lock_path,
                lock_timeout=0.2,
            )
    finally:
        holder.release()


def test_sync_podcast_default_lock_path_derived_from_state_path(
    monkeypatch, patched_pipeline, tmp_path
):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])

    _fetch(library_root=tmp_path / "library", state_db_path=tmp_path / "state" / "state.sqlite")

    assert (tmp_path / "state" / ".podcasts.lock").exists()


def test_sync_shows_captures_lock_timeout_as_error_outcome(
    monkeypatch, patched_pipeline, tmp_path
):
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
    lock_path = tmp_path / ".podcasts.lock"

    holder = FileLock(lock_path, timeout=5)
    holder.acquire()
    try:
        outcomes = download_module.sync_shows(
            [PODCAST],
            token="tok",
            library_root=tmp_path / "library",
            state_db_path=tmp_path / "state.sqlite",
            lock_path=lock_path,
            lock_timeout=0.2,
        )
    finally:
        holder.release()

    assert outcomes[0].error is not None
    assert outcomes[0].result is None


# --- prune_unsubscribed_shows ------------------------------------------------


def test_prune_deletes_file_and_flags_episode_for_unsubscribed_show(tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    episode_path = tmp_path / "library" / "Old Show" / "episode.mp3"
    episode_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", episode_path)
    _record_existing_episode(
        state_db_path,
        episode_uuid="ep-1",
        local_path=episode_path,
        played=False,
        podcast_uuid="show-gone",
    )

    pruned = download_module.prune_unsubscribed_shows(
        [PodcastSummary(uuid="show-still-subscribed", title="Other", author="")],
        state_db_path=state_db_path,
    )

    assert [e.episode_uuid for e in pruned] == ["ep-1"]
    assert not episode_path.exists()
    with StateDB(state_db_path) as db:
        assert db.get_episode("ep-1").unsubscribed is True


def test_prune_leaves_subscribed_show_alone(tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    episode_path = tmp_path / "library" / "Still Here" / "episode.mp3"
    episode_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", episode_path)
    _record_existing_episode(
        state_db_path,
        episode_uuid="ep-1",
        local_path=episode_path,
        played=False,
        podcast_uuid="show-1",
    )

    pruned = download_module.prune_unsubscribed_shows(
        [PodcastSummary(uuid="show-1", title="Still Here", author="")],
        state_db_path=state_db_path,
    )

    assert pruned == []
    assert episode_path.exists()
    with StateDB(state_db_path) as db:
        assert db.get_episode("ep-1").unsubscribed is False


def test_prune_is_idempotent_across_runs(tmp_path):
    # Second run must not re-report or re-attempt an already-pruned episode.
    state_db_path = tmp_path / "state.sqlite"
    episode_path = tmp_path / "library" / "Old Show" / "episode.mp3"
    episode_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", episode_path)
    _record_existing_episode(
        state_db_path,
        episode_uuid="ep-1",
        local_path=episode_path,
        played=False,
        podcast_uuid="show-gone",
    )

    first = download_module.prune_unsubscribed_shows([], state_db_path=state_db_path)
    second = download_module.prune_unsubscribed_shows([], state_db_path=state_db_path)

    assert len(first) == 1
    assert second == []


def test_prune_does_not_delete_file_still_wanted_by_sibling_profile(tmp_path):
    # Podcast files are shared/deduped across profiles (no profile name in
    # the path) -- profile A unsubscribing must not break profile B, which
    # is still subscribed to the same show and shares the same file.
    state_root = tmp_path / "state"
    state_root.mkdir()
    shared_path = tmp_path / "library" / "Shared Show" / "episode.mp3"
    shared_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", shared_path)

    profile_a_db = state_root / "alice.sqlite"
    profile_b_db = state_root / "bob.sqlite"
    for db_path in (profile_a_db, profile_b_db):
        _record_existing_episode(
            db_path,
            episode_uuid="ep-1",
            local_path=shared_path,
            played=False,
            podcast_uuid="show-shared",
        )

    # Only alice unsubscribes; bob's row is untouched (still not unsubscribed).
    pruned = download_module.prune_unsubscribed_shows([], state_db_path=profile_a_db)

    assert [e.episode_uuid for e in pruned] == ["ep-1"]
    assert shared_path.exists()  # bob still needs it
    with StateDB(profile_a_db) as db:
        assert db.get_episode("ep-1").unsubscribed is True  # alice's own flag still set
    with StateDB(profile_b_db) as db:
        assert db.get_episode("ep-1").unsubscribed is False  # bob's is untouched


def test_prune_narrowed_show_filter_is_not_mistaken_for_unsubscribe(tmp_path):
    # Passing a --show-narrowed list (instead of the full account
    # subscriptions) would wrongly prune every other subscribed show.
    # Callers must always pass the full list -- this documents why.
    state_db_path = tmp_path / "state.sqlite"
    episode_path = tmp_path / "library" / "Still Subscribed" / "episode.mp3"
    episode_path.parent.mkdir(parents=True)
    shutil.copy(FIXTURES / "episode.mp3", episode_path)
    _record_existing_episode(
        state_db_path,
        episode_uuid="ep-1",
        local_path=episode_path,
        played=False,
        podcast_uuid="show-1",
    )

    # Simulates passing a --show-narrowed subset that happens to exclude
    # show-1 for this particular run -- NOT the same as unsubscribing.
    narrowed_subscriptions = [PodcastSummary(uuid="show-2", title="Other", author="")]
    pruned = download_module.prune_unsubscribed_shows(
        narrowed_subscriptions, state_db_path=state_db_path
    )

    # This demonstrates the function has no way to distinguish "narrowed"
    # from "unsubscribed" -- it trusts its input completely, so it DOES
    # prune here. The safety is entirely in the caller passing the full
    # list (see cli.py/orchestrate.py call sites).
    assert [e.episode_uuid for e in pruned] == ["ep-1"]


def test_push_pending_play_status_pushes_and_clears_flag(monkeypatch, tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-1",
                podcast_uuid="show-1",
                show_name="Test Show",
                local_path="/does/not/matter.mp3",
                played=False,
                played_up_to=0,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )
        db.update_play_state("ep-1", played=True, played_up_to=900)

    captured = {}

    def fake_update_episode_status(token, *, episode_uuid, podcast_uuid, played, played_up_to):
        captured["args"] = (token, episode_uuid, podcast_uuid, played, played_up_to)

    monkeypatch.setattr(download_module, "update_episode_status", fake_update_episode_status)

    pushed, failed = download_module.push_pending_play_status(
        "the-token", state_db_path=state_db_path
    )

    assert [e.episode_uuid for e in pushed] == ["ep-1"]
    assert failed == []
    assert captured["args"] == ("the-token", "ep-1", "show-1", True, 900)
    with StateDB(state_db_path) as db:
        assert db.get_episode("ep-1").pending_push is False


def test_push_pending_play_status_skips_when_nothing_pending(tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-1",
                podcast_uuid="show-1",
                show_name="Test Show",
                local_path="/does/not/matter.mp3",
                played=False,
                played_up_to=0,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )

    pushed, failed = download_module.push_pending_play_status(
        "the-token", state_db_path=state_db_path
    )

    assert pushed == []
    assert failed == []


def test_push_pending_play_status_one_failure_does_not_abort_the_rest(monkeypatch, tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    with StateDB(state_db_path) as db:
        for uuid in ("ep-1", "ep-2"):
            db.record_episode(
                EpisodeRecord(
                    episode_uuid=uuid,
                    podcast_uuid="show-1",
                    show_name="Test Show",
                    local_path=f"/does/not/matter-{uuid}.mp3",
                    played=False,
                    played_up_to=0,
                    downloaded_at="2026-07-19T00:00:00+00:00",
                )
            )
            db.update_play_state(uuid, played=True, played_up_to=100)

    def fake_update_episode_status(token, *, episode_uuid, podcast_uuid, played, played_up_to):
        if episode_uuid == "ep-1":
            raise httpx.HTTPError("network blip")

    monkeypatch.setattr(download_module, "update_episode_status", fake_update_episode_status)

    pushed, failed = download_module.push_pending_play_status(
        "the-token", state_db_path=state_db_path
    )

    assert [e.episode_uuid for e in pushed] == ["ep-2"]
    assert [e.episode_uuid for e, _err in failed] == ["ep-1"]
    with StateDB(state_db_path) as db:
        # The failed push must stay pending so a later run retries it.
        assert db.get_episode("ep-1").pending_push is True
        assert db.get_episode("ep-2").pending_push is False


def _record_episode_needing_backfill(
    state_db_path, *, episode_uuid, audio_url, podcast_uuid="show-1", **overrides
):
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid=episode_uuid,
                podcast_uuid=podcast_uuid,
                show_name="Test Show",
                local_path=f"/does/not/matter-{episode_uuid}.mp3",
                played=overrides.get("played", False),
                played_up_to=overrides.get("played_up_to", 0),
                downloaded_at="2026-07-19T00:00:00+00:00",
                audio_url=audio_url,
                description=overrides.get("description", ""),
                episode_number=overrides.get("episode_number"),
                season_number=overrides.get("season_number"),
                published_at=overrides.get("published_at", ""),
            )
        )


def test_backfill_episode_metadata_updates_episodes_needing_it(monkeypatch, tmp_path):
    from podcast_manager.rss import RssEpisodeMeta

    state_db_path = tmp_path / "state.sqlite"
    _record_episode_needing_backfill(
        state_db_path, episode_uuid="ep-1", audio_url="https://cdn.example/ep1.mp3"
    )

    monkeypatch.setattr(
        download_module, "resolve_feed_url", lambda title, author: "https://example.com/feed.xml"
    )
    monkeypatch.setattr(
        download_module,
        "fetch_rss_episodes",
        lambda feed_url: [
            RssEpisodeMeta(
                enclosure_url="https://cdn.example/ep1.mp3",
                title="Ep 1",
                description="Backfilled.",
                episode_number=3,
                season_number=1,
                published="Sun, 01 Mar 2026 00:00:00 -0000",
            )
        ],
    )

    result = download_module.backfill_episode_metadata(
        [PODCAST], state_db_path=state_db_path
    )

    assert [e.episode_uuid for e in result.updated] == ["ep-1"]
    assert result.unresolved_feeds == []
    assert result.unmatched == 0
    with StateDB(state_db_path) as db:
        fetched = db.get_episode("ep-1")
        assert fetched.description == "Backfilled."
        assert fetched.episode_number == 3
        assert fetched.season_number == 1


def test_backfill_episode_metadata_does_not_touch_play_state(monkeypatch, tmp_path):
    from podcast_manager.rss import RssEpisodeMeta

    state_db_path = tmp_path / "state.sqlite"
    _record_episode_needing_backfill(
        state_db_path,
        episode_uuid="ep-1",
        audio_url="https://cdn.example/ep1.mp3",
        played=True,
        played_up_to=900,
    )

    monkeypatch.setattr(
        download_module, "resolve_feed_url", lambda title, author: "https://example.com/feed.xml"
    )
    monkeypatch.setattr(
        download_module,
        "fetch_rss_episodes",
        lambda feed_url: [
            RssEpisodeMeta(
                enclosure_url="https://cdn.example/ep1.mp3",
                title="Ep 1",
                description="Backfilled.",
                episode_number=None,
                season_number=None,
                published=None,
            )
        ],
    )

    download_module.backfill_episode_metadata([PODCAST], state_db_path=state_db_path)

    with StateDB(state_db_path) as db:
        fetched = db.get_episode("ep-1")
        assert fetched.played is True
        assert fetched.played_up_to == 900


def test_backfill_episode_metadata_skips_shows_with_nothing_to_backfill(monkeypatch, tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    _record_episode_needing_backfill(
        state_db_path,
        episode_uuid="ep-1",
        audio_url="https://cdn.example/ep1.mp3",
        description="Already enriched.",
    )

    calls = []
    monkeypatch.setattr(
        download_module,
        "resolve_feed_url",
        lambda title, author: calls.append(1) or "https://example.com/feed.xml",
    )

    result = download_module.backfill_episode_metadata([PODCAST], state_db_path=state_db_path)

    assert calls == []  # never even attempted a feed lookup
    assert result.updated == []


def test_backfill_episode_metadata_records_unresolved_feed(monkeypatch, tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    _record_episode_needing_backfill(
        state_db_path, episode_uuid="ep-1", audio_url="https://cdn.example/ep1.mp3"
    )
    monkeypatch.setattr(download_module, "resolve_feed_url", lambda title, author: None)

    result = download_module.backfill_episode_metadata([PODCAST], state_db_path=state_db_path)

    assert result.unresolved_feeds == ["Test Show"]
    assert result.updated == []
    with StateDB(state_db_path) as db:
        assert db.get_episode("ep-1").description == ""


def test_backfill_episode_metadata_counts_unmatched_episodes(monkeypatch, tmp_path):
    state_db_path = tmp_path / "state.sqlite"
    _record_episode_needing_backfill(
        state_db_path, episode_uuid="ep-1", audio_url="https://cdn.example/no-match.mp3"
    )
    monkeypatch.setattr(
        download_module, "resolve_feed_url", lambda title, author: "https://example.com/feed.xml"
    )
    monkeypatch.setattr(download_module, "fetch_rss_episodes", lambda feed_url: [])

    result = download_module.backfill_episode_metadata([PODCAST], state_db_path=state_db_path)

    assert result.unmatched == 1
    assert result.updated == []
