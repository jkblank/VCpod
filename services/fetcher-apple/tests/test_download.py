import os
import shutil
import subprocess
from pathlib import Path

import pytest

from gamdl.utils import GamdlError

from common.lock import FileLock, LockTimeoutError
from common.models import PlaylistEntry
from common.state import StateDB
from fetcher_apple import download as download_module
from fetcher_apple.api import TrackMeta
from fetcher_apple.download import DownloadError
from fetcher_apple.tag import read_dedup_tags

FIXTURES = Path(__file__).resolve().parent / "fixtures"

TRACKS = [
    TrackMeta(source_id="song-1", title="Track One", artist="Artist One"),
    TrackMeta(source_id="song-2", title="Track Two", artist="Artist Two"),
]


def _fake_gamdl_run(library_root: Path, scratch_dir: Path):
    """Simulates what a real `gamdl --save-playlist` invocation leaves behind:
    downloaded audio files under the album-folder layout, plus a .m3u file
    in the scratch dir with paths relative to that file.
    """

    def _run(cmd, capture_output, text):
        album_dir = library_root / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        track1 = album_dir / "01 Track One.m4a"
        track2 = album_dir / "02 Track Two.m4a"
        shutil.copy(FIXTURES / "track1.m4a", track1)
        shutil.copy(FIXTURES / "track2.m4a", track2)

        scratch_dir.mkdir(parents=True, exist_ok=True)
        m3u = scratch_dir / "playlist.m3u"
        rel1 = Path(os.path.relpath(track1, start=scratch_dir))
        rel2 = Path(os.path.relpath(track2, start=scratch_dir))
        m3u.write_text(f"{rel1.as_posix()}\n{rel2.as_posix()}\n", encoding="utf8")

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


def _fake_gamdl_run_reordered(library_root: Path, scratch_dir: Path):
    """Reproduces the real bug found via live testing: gamdl's .m3u line order
    doesn't necessarily match the order tracks were fetched in (e.g. a skipped
    track shifts everything after it). Here the .m3u lists Track Two's file
    before Track One's, the reverse of TRACKS' order.
    """

    def _run(cmd, capture_output, text):
        album_dir = library_root / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        track1 = album_dir / "01 Track One.m4a"
        track2 = album_dir / "02 Track Two.m4a"
        shutil.copy(FIXTURES / "track1.m4a", track1)
        shutil.copy(FIXTURES / "track2.m4a", track2)

        scratch_dir.mkdir(parents=True, exist_ok=True)
        m3u = scratch_dir / "playlist.m3u"
        rel1 = Path(os.path.relpath(track1, start=scratch_dir))
        rel2 = Path(os.path.relpath(track2, start=scratch_dir))
        # reversed order relative to TRACKS (song-1, song-2)
        m3u.write_text(f"{rel2.as_posix()}\n{rel1.as_posix()}\n", encoding="utf8")

        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


@pytest.fixture
def patched_pipeline(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )

    scratch_dir = library_root / "_gamdl_scratch" / "pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    monkeypatch.setattr(
        subprocess, "run", _fake_gamdl_run(library_root, scratch_dir)
    )

    return {
        "library_root": library_root,
        "playlists_root": playlists_root,
        "state_db_path": state_db_path,
    }


def test_fetch_playlist_tags_records_and_writes_m3u8(patched_pipeline):
    result = download_module.fetch_playlist(
        playlist_name="ALT CTRL",
        playlist_source_id="pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    assert len(result.new_tracks) == 2
    assert len(result.already_known_tracks) == 0

    for record in result.new_tracks:
        source, source_id = read_dedup_tags(record.local_path)
        assert source == "apple_music"
        assert source_id == record.source_id

    with StateDB(patched_pipeline["state_db_path"]) as db:
        assert db.get_track("apple_music", "song-1") is not None
        assert db.get_track("apple_music", "song-2") is not None

    assert result.m3u8_path == patched_pipeline["playlists_root"] / "john" / "ALT CTRL.m3u8"
    lines = result.m3u8_path.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert len(lines) == 3

    scratch_dir = patched_pipeline["library_root"] / "_gamdl_scratch" / "pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert not scratch_dir.exists()


def test_fetch_playlist_matches_by_tag_content_not_m3u_position(monkeypatch, tmp_path):
    # Regression test for a real bug: pairing gamdl's .m3u lines with our
    # pre-fetched track list by position assigned wrong source_ids when the
    # .m3u came back in a different order.
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    scratch_dir = library_root / "_gamdl_scratch" / "pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    monkeypatch.setattr(
        subprocess, "run", _fake_gamdl_run_reordered(library_root, scratch_dir)
    )

    result = download_module.fetch_playlist(
        playlist_name="ALT CTRL",
        playlist_source_id="pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile="john",
        cookies_path="cookies.txt",
        library_root=library_root,
        playlists_root=playlists_root,
        state_db_path=state_db_path,
    )

    assert len(result.unmatched_paths) == 0
    with StateDB(state_db_path) as db:
        song1 = db.get_track("apple_music", "song-1")
        song2 = db.get_track("apple_music", "song-2")
        assert song1.local_path.endswith("01 Track One.m4a")
        assert song2.local_path.endswith("02 Track Two.m4a")

    source, source_id = read_dedup_tags(
        library_root / "Artist One" / "Album One" / "01 Track One.m4a"
    )
    assert source_id == "song-1"
    source, source_id = read_dedup_tags(
        library_root / "Artist One" / "Album One" / "02 Track Two.m4a"
    )
    assert source_id == "song-2"


def test_fetch_playlist_skips_already_known_tracks_on_rerun(patched_pipeline):
    download_module.fetch_playlist(
        playlist_name="ALT CTRL",
        playlist_source_id="pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    second = download_module.fetch_playlist(
        playlist_name="ALT CTRL",
        playlist_source_id="pl.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        profile="john",
        cookies_path="cookies.txt",
        library_root=patched_pipeline["library_root"],
        playlists_root=patched_pipeline["playlists_root"],
        state_db_path=patched_pipeline["state_db_path"],
    )

    assert len(second.new_tracks) == 0
    assert len(second.already_known_tracks) == 2
    # playlist file is still written in full even when tracks were already known
    lines = second.m3u8_path.read_text().splitlines()
    assert len(lines) == 3


# --- per-track fallback (for playlist ids gamdl's own CLI can't parse,
# e.g. Apple's "pl.pm-" personalized Mix playlists) -------------------------

PM_PLAYLIST_ID = "pl.pm-20e9f373919da080f80c0eceb6aae553"


def test_gamdl_can_parse_playlist_id_accepts_known_shapes():
    assert download_module._gamdl_can_parse_playlist_id("pl." + "a" * 32) is True
    assert download_module._gamdl_can_parse_playlist_id("pl.u-abc123") is True


def test_gamdl_can_parse_playlist_id_rejects_personal_mix():
    assert download_module._gamdl_can_parse_playlist_id(PM_PLAYLIST_ID) is False


def _fake_gamdl_single_track_run(fixture_by_id: dict[str, str]):
    """Simulates a single-track `gamdl <song-url> --output-path <scratch>`
    invocation: writes the fixture audio file under the album-folder layout
    if the requested song id is "available", else fails like a real
    unavailable/region-restricted track would."""

    def _run(cmd, capture_output, text):
        url = cmd[1]
        song_id = url.rsplit("/", 1)[-1]
        output_path = Path(cmd[cmd.index("--output-path") + 1])
        fixture_name = fixture_by_id.get(song_id)
        if fixture_name is None:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="track unavailable")
        album_dir = output_path / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / fixture_name, album_dir / f"{song_id}.m4a")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


def _fetch_pm(**overrides):
    kwargs = dict(
        playlist_name="Chill",
        playlist_source_id=PM_PLAYLIST_ID,
        profile="john",
        cookies_path="cookies.txt",
    )
    kwargs.update(overrides)
    return download_module.fetch_playlist(**kwargs)


def test_fetch_playlist_routes_pm_playlist_to_per_track_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )

    def _fail_if_called(**kwargs):
        raise AssertionError("should not use the playlist-URL path for a pl.pm- id")

    monkeypatch.setattr(download_module, "_fetch_via_playlist_url", _fail_if_called)
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_gamdl_single_track_run({"song-1": "track1.m4a", "song-2": "track2.m4a"}),
    )

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 2


def test_fetch_per_track_downloads_and_records_in_order(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_gamdl_single_track_run({"song-1": "track1.m4a", "song-2": "track2.m4a"}),
    )

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 2
    assert [r.source_id for r in result.new_tracks] == ["song-1", "song-2"]
    for record in result.new_tracks:
        path = Path(record.local_path)
        assert path.exists()
        source, source_id = read_dedup_tags(path)
        assert source == "apple_music"
        assert source_id == record.source_id

    scratch_root = tmp_path / "library" / "_gamdl_scratch"
    assert not scratch_root.exists() or not any(scratch_root.iterdir())

    lines = result.m3u8_path.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert len(lines) == 3


def test_fetch_per_track_m3u8_paths_are_absolute_even_with_relative_library_root(
    monkeypatch, tmp_path
):
    # Confirmed live: playlists routed through the per-track fallback
    # (Chill, New Music) wrote relative paths into their .m3u8 when
    # library_root was passed in relative — unlike _fetch_via_playlist_url,
    # which always resolves via gamdl's .m3u output. iOpenPod's
    # playlist-file sync skipped nearly every entry in both as a result.
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_gamdl_single_track_run({"song-1": "track1.m4a", "song-2": "track2.m4a"}),
    )
    monkeypatch.chdir(tmp_path)

    result = _fetch_pm(
        library_root=Path("library"),  # relative, matching the real-world CLI call
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    lines = result.m3u8_path.read_text().splitlines()
    for line in lines[1:]:
        assert Path(line).is_absolute(), f"expected absolute path, got {line!r}"


def test_fetch_per_track_skips_already_known_without_reinvoking_gamdl(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    call_count = {"n": 0}
    fake_run = _fake_gamdl_single_track_run({"song-1": "track1.m4a", "song-2": "track2.m4a"})

    def _counting_run(cmd, capture_output, text):
        call_count["n"] += 1
        return fake_run(cmd, capture_output, text)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"

    _fetch_pm(library_root=library_root, playlists_root=playlists_root, state_db_path=state_db_path)
    assert call_count["n"] == 2

    second = _fetch_pm(
        library_root=library_root, playlists_root=playlists_root, state_db_path=state_db_path
    )
    assert call_count["n"] == 2  # no new gamdl invocations on rerun
    assert len(second.already_known_tracks) == 2
    assert len(second.new_tracks) == 0


def test_fetch_per_track_handles_failed_track_gracefully(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    monkeypatch.setattr(
        subprocess, "run", _fake_gamdl_single_track_run({"song-1": "track1.m4a"})  # song-2 fails
    )

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 1
    assert result.new_tracks[0].source_id == "song-1"
    assert len(result.failed_tracks) == 1
    assert result.failed_tracks[0].source_id == "song-2"

    lines = result.m3u8_path.read_text().splitlines()
    assert len(lines) == 2  # header + only the successfully downloaded track


def test_fetch_per_track_handles_ambiguous_output_as_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS[:1]
    )

    def _run(cmd, capture_output, text):
        output_path = Path(cmd[cmd.index("--output-path") + 1])
        album_dir = output_path / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(FIXTURES / "track1.m4a", album_dir / "a.m4a")
        shutil.copy(FIXTURES / "track2.m4a", album_dir / "b.m4a")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 0
    assert len(result.failed_tracks) == 1


def test_fetch_per_track_moves_lrc_sidecar_alongside_audio(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS[:1]
    )

    def _run(cmd, capture_output, text):
        output_path = Path(cmd[cmd.index("--output-path") + 1])
        album_dir = output_path / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        audio = album_dir / "01 Track One.m4a"
        shutil.copy(FIXTURES / "track1.m4a", audio)
        audio.with_suffix(".lrc").write_text("[00:00.00]la la la\n", encoding="utf8")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 1
    dest = Path(result.new_tracks[0].local_path)
    assert dest.exists()
    assert dest.with_suffix(".lrc").exists()


def test_fetch_per_track_disambiguates_colliding_filenames(monkeypatch, tmp_path):
    collider = TrackMeta(source_id="song-2", title="Track One", artist="Artist One")
    monkeypatch.setattr(
        download_module,
        "get_playlist_tracks",
        lambda cookies_path, source_id: [TRACKS[0], collider],
    )

    def _run(cmd, capture_output, text):
        url = cmd[1]
        song_id = url.rsplit("/", 1)[-1]
        output_path = Path(cmd[cmd.index("--output-path") + 1])
        album_dir = output_path / "Artist One" / "Album One"
        album_dir.mkdir(parents=True, exist_ok=True)
        # both tracks resolve to the identical filename from gamdl's own naming
        dest = album_dir / "01 Track One.m4a"
        shutil.copy(FIXTURES / ("track1.m4a" if song_id == "song-1" else "track2.m4a"), dest)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _run)

    result = _fetch_pm(
        library_root=tmp_path / "library",
        playlists_root=tmp_path / "playlists",
        state_db_path=tmp_path / "state.sqlite",
    )

    assert len(result.new_tracks) == 2
    paths = {r.source_id: Path(r.local_path) for r in result.new_tracks}
    assert paths["song-1"] != paths["song-2"]
    assert paths["song-1"].exists()
    assert paths["song-2"].exists()


# --- Apple Music session lock -----------------------------------------------


def test_fetch_playlist_raises_lock_timeout_when_another_session_active(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    lock_path = tmp_path / ".apple_music.lock"

    holder = FileLock(lock_path, timeout=5)
    holder.acquire()
    try:
        with pytest.raises(LockTimeoutError):
            download_module.fetch_playlist(
                playlist_name="ALT CTRL",
                playlist_source_id="pl." + "a" * 32,
                profile="john",
                cookies_path="cookies.txt",
                library_root=tmp_path / "library",
                playlists_root=tmp_path / "playlists",
                state_db_path=tmp_path / "state.sqlite",
                lock_path=lock_path,
                lock_timeout=0.2,
            )
    finally:
        holder.release()


def test_fetch_playlist_wraps_gamdl_api_error_as_download_error(monkeypatch, tmp_path):
    # Confirmed live: an expired/invalid Apple Music cookies file makes
    # gamdl's own API client raise GamdlApiResponseError directly out of
    # get_playlist_tracks() — uncaught, this crashed the whole
    # music-stack-cli sync-everything run instead of being isolated to
    # just the apple_music source.
    def _raise(cookies_path, source_id):
        raise GamdlError("Error fetching account info")

    monkeypatch.setattr(download_module, "get_playlist_tracks", _raise)

    with pytest.raises(DownloadError):
        download_module.fetch_playlist(
            playlist_name="ALT CTRL",
            playlist_source_id="pl." + "a" * 32,
            profile="john",
            cookies_path="cookies.txt",
            library_root=tmp_path / "library",
            playlists_root=tmp_path / "playlists",
            state_db_path=tmp_path / "state.sqlite",
        )


def test_fetch_playlists_isolates_gamdl_api_error_to_one_entry(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    good_id = "pl." + "a" * 32
    bad_id = "pl." + "b" * 32

    def _get_playlist_tracks(cookies_path, source_id):
        if source_id == bad_id:
            raise GamdlError("Error fetching account info")
        return TRACKS

    monkeypatch.setattr(download_module, "get_playlist_tracks", _get_playlist_tracks)
    monkeypatch.setattr(
        subprocess, "run", _fake_gamdl_run(library_root, library_root / "_gamdl_scratch" / good_id)
    )

    entries = [
        PlaylistEntry(name="Bad", source="apple_music", source_id=bad_id),
        PlaylistEntry(name="Good", source="apple_music", source_id=good_id),
    ]

    outcomes = download_module.fetch_playlists(
        entries,
        profile="john",
        cookies_path="cookies.txt",
        library_root=library_root,
        playlists_root=playlists_root,
        state_db_path=state_db_path,
    )

    bad, good = outcomes
    assert bad.result is None
    assert "Apple Music" in bad.error
    assert good.error is None
    assert len(good.result.new_tracks) == 2


def test_fetch_playlists_syncs_every_entry_and_isolates_failures(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )

    good_id = "pl." + "a" * 32
    bad_id = "pl." + "b" * 32

    def _run(cmd, capture_output, text):
        if bad_id in " ".join(cmd):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")
        return _fake_gamdl_run(library_root, library_root / "_gamdl_scratch" / good_id)(
            cmd, capture_output, text
        )

    monkeypatch.setattr(subprocess, "run", _run)

    entries = [
        PlaylistEntry(name="Good", source="apple_music", source_id=good_id),
        PlaylistEntry(name="Bad", source="apple_music", source_id=bad_id),
    ]

    outcomes = download_module.fetch_playlists(
        entries,
        profile="john",
        cookies_path="cookies.txt",
        library_root=library_root,
        playlists_root=playlists_root,
        state_db_path=state_db_path,
    )

    assert [o.entry.name for o in outcomes] == ["Good", "Bad"]
    good, bad = outcomes
    assert good.error is None
    assert len(good.result.new_tracks) == 2
    assert bad.result is None
    assert bad.error is not None  # a whole-playlist gamdl failure raises DownloadError


def test_fetch_playlists_lock_timeout_on_one_entry_does_not_abort_the_rest(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    lock_path = tmp_path / ".apple_music.lock"
    holder = FileLock(lock_path, timeout=5)
    holder.acquire()
    try:
        entries = [
            PlaylistEntry(name="Locked", source="apple_music", source_id="pl." + "a" * 32),
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


def test_fetch_playlist_default_lock_path_derived_from_state_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        download_module, "get_playlist_tracks", lambda cookies_path, source_id: TRACKS
    )
    library_root = tmp_path / "library"
    library_root.mkdir()
    scratch_dir = library_root / "_gamdl_scratch" / ("pl." + "a" * 32)
    monkeypatch.setattr(subprocess, "run", _fake_gamdl_run(library_root, scratch_dir))

    state_db_path = tmp_path / "state" / "john.sqlite"

    result = download_module.fetch_playlist(
        playlist_name="ALT CTRL",
        playlist_source_id="pl." + "a" * 32,
        profile="john",
        cookies_path="cookies.txt",
        library_root=library_root,
        playlists_root=tmp_path / "playlists",
        state_db_path=state_db_path,
    )

    assert len(result.new_tracks) == 2
    assert (tmp_path / "state" / ".apple_music.lock").exists()
