import shutil
from pathlib import Path

import httpx
import pytest

from common.state import StateDB
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


def test_sync_podcast_orders_newest_first_regardless_of_input_order(monkeypatch, tmp_path):
    shuffled = [FULL_EPISODES[2], FULL_EPISODES[0], FULL_EPISODES[1]]
    monkeypatch.setattr(download_module, "list_full_episodes", lambda token, uuid: shuffled)
    monkeypatch.setattr(download_module, "list_episode_states", lambda token, uuid: [])
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
    assert not dest.with_suffix(dest.suffix + ".part").exists()
