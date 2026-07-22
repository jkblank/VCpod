import shutil
import subprocess
from pathlib import Path

import pytest

from common.lock import FileLock
from common.models import PlaylistEntry
from common.state import StateDB
from fetcher_ytmusic import download as download_module
from fetcher_ytmusic.api import TrackMeta
from fetcher_ytmusic.tag import read_basic_tags, read_dedup_tags

FIXTURES = Path(__file__).resolve().parent / "fixtures"

TRACKS = [
    TrackMeta(source_id="vid-1", title="Track One", artist="Artist One", album="Album One"),
    TrackMeta(source_id="vid-2", title="Track Two", artist="Artist Two", album="Album Two"),
]


def _fake_ytdlp_run(fixture_name: str = "track1.m4a"):
    """Simulates what a real yt-dlp -x --audio-format m4a invocation leaves
    behind: a single output file at the -o template's directory."""

    def _run(cmd, capture_output, text):
        o_idx = cmd.index("-o")
        template = Path(cmd[o_idx + 1])
        scratch_dir = template.parent
        scratch_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / fixture_name, scratch_dir / "vid.m4a")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


def _fake_ytdlp_run_one_track_fails(failing_video_id: str):
    def _run(cmd, capture_output, text):
        if failing_video_id in cmd[-1]:
            return subprocess.CompletedProcess(
                cmd, 1, stdout="", stderr="ERROR: Sign in to confirm you're not a bot"
            )
        o_idx = cmd.index("-o")
        template = Path(cmd[o_idx + 1])
        scratch_dir = template.parent
        scratch_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "track1.m4a", scratch_dir / "vid.m4a")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


@pytest.fixture
def patched_pipeline(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda playlist_id, oauth_path=None: TRACKS
    )
    monkeypatch.setattr(subprocess, "run", _fake_ytdlp_run())

    return {
        "library_root": library_root,
        "playlists_root": playlists_root,
        "state_db_path": state_db_path,
    }


def test_fetch_playlist_tags_records_and_writes_m3u8(patched_pipeline):
    result = download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    assert len(result.new_tracks) == 2
    assert len(result.already_known_tracks) == 0
    assert result.failed_tracks == []

    for record in result.new_tracks:
        source, source_id = read_dedup_tags(record.local_path)
        assert source == "ytmusic"
        assert source_id == record.source_id

    title, artist = read_basic_tags(
        patched_pipeline["library_root"] / "Artist One" / "Album One" / "Track One.m4a"
    )
    assert title == "Track One"
    assert artist == "Artist One"

    with StateDB(patched_pipeline["state_db_path"]) as db:
        assert db.get_track("ytmusic", "vid-1") is not None
        assert db.get_track("ytmusic", "vid-2") is not None

    assert (
        result.m3u8_path == patched_pipeline["playlists_root"] / "john" / "Semaphore.m3u8"
    )
    lines = result.m3u8_path.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert len(lines) == 3

    scratch_dir = patched_pipeline["library_root"] / "_ytdlp_scratch"
    assert not any(scratch_dir.glob("*/*"))


def test_fetch_playlist_skips_already_known_tracks_on_rerun(patched_pipeline):
    download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    second = download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    assert len(second.new_tracks) == 0
    assert len(second.already_known_tracks) == 2
    lines = second.m3u8_path.read_text().splitlines()
    assert len(lines) == 3


def test_fetch_playlist_one_failed_track_does_not_abort_rest(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda playlist_id, oauth_path=None: TRACKS
    )
    monkeypatch.setattr(subprocess, "run", _fake_ytdlp_run_one_track_fails("vid-1"))

    result = download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=library_root,
        playlists_root=playlists_root,
        state_db_path=state_db_path,
    )

    assert len(result.new_tracks) == 1
    assert result.new_tracks[0].source_id == "vid-2"
    assert len(result.failed_tracks) == 1
    assert result.failed_tracks[0][0].source_id == "vid-1"


def test_fetch_playlist_additive_mode_preserves_existing_m3u8_entries(
    monkeypatch, patched_pipeline
):
    m3u8_path = patched_pipeline["playlists_root"] / "john" / "Semaphore.m3u8"
    m3u8_path.parent.mkdir(parents=True)
    m3u8_path.write_text("#EXTM3U\n/already/there.m4a\n")

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda playlist_id, oauth_path=None: TRACKS[:1]
    )

    result = download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
        sync_mode="additive",
    )

    lines = result.m3u8_path.read_text().splitlines()
    assert "/already/there.m4a" in lines


def test_fetch_playlist_m3u8_paths_are_absolute_even_with_relative_library_root(
    monkeypatch, tmp_path
):
    # Confirmed live: a real "Semaphore" playlist synced with a relative
    # --library-root wrote relative paths into its .m3u8 — iOpenPod's
    # playlist-file matching compares against absolute paths from its own
    # PC scan, so every entry came back skipped_count == total_entries,
    # items == [] (the playlist was created on the device but empty).
    # Same bug class as fetcher-apple's per-track fallback fix.
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda playlist_id, oauth_path=None: TRACKS
    )
    monkeypatch.setattr(subprocess, "run", _fake_ytdlp_run())
    monkeypatch.chdir(tmp_path)
    (tmp_path / "library").mkdir()

    result = download_module.fetch_playlist(
        playlist_name="Semaphore",
        playlist_source_id="PLxxxxxxxxxxxxxxxxxxxx",
        profile="john",
        cookies_path="cookies.txt",
        library_root=Path("library"),  # relative, matching the real-world CLI call
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    lines = result.m3u8_path.read_text().splitlines()
    for line in lines[1:]:
        assert Path(line).is_absolute(), f"expected absolute path, got {line!r}"


def test_fetch_playlists_syncs_every_entry(patched_pipeline):
    entries = [
        PlaylistEntry(name="Semaphore", source="ytmusic", source_id="PLxxxxxxxxxxxxxxxxxxxx"),
        PlaylistEntry(name="Other List", source="ytmusic", source_id="PLyyyyyyyyyyyyyyyyyyyy"),
    ]

    outcomes = download_module.fetch_playlists(
        entries,
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    assert [o.entry.name for o in outcomes] == ["Semaphore", "Other List"]
    assert all(o.error is None for o in outcomes)
    # Both playlists share TRACKS' source_ids in this fixture — the state db
    # is shared across playlists in one profile, so the second entry's
    # tracks are correctly recognized as already-known, not re-downloaded.
    assert len(outcomes[0].result.new_tracks) == 2
    assert len(outcomes[1].result.already_known_tracks) == 2


def test_fetch_playlists_lock_timeout_on_one_entry_does_not_abort_the_rest(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda playlist_id, oauth_path=None: TRACKS
    )
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    lock_path = tmp_path / ".ytmusic.lock"
    holder = FileLock(lock_path, timeout=5)
    holder.acquire()
    try:
        entries = [
            PlaylistEntry(name="Semaphore", source="ytmusic", source_id="PLxxxxxxxxxxxxxxxxxxxx"),
        ]
        outcomes = download_module.fetch_playlists(
            entries,
            profile="john",
            cookies_path="cookies.txt",
            library_root=library_root,
            playlists_root=playlists_root,
            state_db_path=state_db_path,
            lock_path=lock_path,
            lock_timeout=0.2,
        )
    finally:
        holder.release()

    assert outcomes[0].result is None
    assert outcomes[0].error is not None
