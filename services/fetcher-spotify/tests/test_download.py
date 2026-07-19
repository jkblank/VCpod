import shutil
import subprocess
from pathlib import Path

import pytest

from common.state import StateDB, TrackRecord
from fetcher_spotify import download as download_module
from fetcher_spotify.api import TrackMeta
from fetcher_spotify.tag import read_dedup_tags, read_isrc_tag

FIXTURES = Path(__file__).resolve().parent / "fixtures"

TRACK1 = TrackMeta(
    source_id="song-1",
    title="Track One",
    artist="Artist One",
    album="Album One",
    track_number=1,
    isrc="USRC00000001",
)
TRACK2 = TrackMeta(
    source_id="song-2", title="Track Two", artist="Artist Two", album="Album Two", track_number=2
)  # no isrc — some tracks legitimately lack one


def _fake_zotify_run(fixture_by_id: dict[str, str]):
    """Simulates zotify downloading a single track into whatever
    --album-library scratch dir it's pointed at for that invocation. A track
    id absent from fixture_by_id simulates a real per-track failure (no file
    produced)."""

    def _run(cmd, capture_output, text):
        url = cmd[1]
        track_id = url.rsplit("/", 1)[-1]
        scratch_dir = Path(cmd[cmd.index("--album-library") + 1])
        fixture_name = fixture_by_id.get(track_id)
        if fixture_name is not None:
            scratch_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy(FIXTURES / fixture_name, scratch_dir / "track.mp3")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return _run


@pytest.fixture
def pipeline(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_db_path = tmp_path / "state.sqlite"
    library_root.mkdir()

    monkeypatch.setattr(
        download_module,
        "get_playlist_tracks",
        lambda credentials_path, source_id: [TRACK1, TRACK2],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_zotify_run({"song-1": "track1.mp3", "song-2": "track2.mp3"}),
    )

    return {
        "library_root": library_root,
        "playlists_root": playlists_root,
        "state_db_path": state_db_path,
    }


def _fetch(pipeline, playlist_name="ALT CTRL"):
    return download_module.fetch_playlist(
        playlist_name=playlist_name,
        playlist_source_id="p1",
        profile="john",
        credentials_path="creds.json",
        library_root=pipeline["library_root"],
        playlists_root=pipeline["playlists_root"],
        state_db_path=pipeline["state_db_path"],
    )


def test_fetch_playlist_tags_records_and_writes_m3u8(pipeline):
    result = _fetch(pipeline)

    assert len(result.new_tracks) == 2
    assert len(result.already_known_tracks) == 0
    assert len(result.unmatched_tracks) == 0

    for record in result.new_tracks:
        path = Path(record.local_path)
        assert path.exists()
        source, source_id = read_dedup_tags(path)
        assert source == "spotify"
        assert source_id == record.source_id

    track1_record = next(r for r in result.new_tracks if r.source_id == "song-1")
    assert track1_record.local_path.endswith("Artist One/Album One/01 Track One.mp3")
    assert read_isrc_tag(track1_record.local_path) == "USRC00000001"

    track2_record = next(r for r in result.new_tracks if r.source_id == "song-2")
    assert read_isrc_tag(track2_record.local_path) is None  # TRACK2 has no isrc

    lines = result.m3u8_path.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert len(lines) == 3

    scratch_dir = pipeline["library_root"] / "_zotify_scratch"
    assert not any(scratch_dir.rglob("*")) if scratch_dir.exists() else True


def test_fetch_playlist_skips_already_known_without_reinvoking_zotify(
    monkeypatch, pipeline
):
    _fetch(pipeline)

    call_count = {"n": 0}
    original = subprocess.run

    def _counting_run(*args, **kwargs):
        call_count["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _counting_run)

    second = _fetch(pipeline)

    assert call_count["n"] == 0  # nothing pending, zotify never invoked
    assert len(second.new_tracks) == 0
    assert len(second.already_known_tracks) == 2
    lines = second.m3u8_path.read_text().splitlines()
    assert len(lines) == 3


def test_fetch_playlist_preserves_order_with_mixed_known_and_new(pipeline):
    # Pre-seed the state db so TRACK2 is already known but TRACK1 is not,
    # then confirm the m3u8 still comes out in tracks_meta order (TRACK1,
    # TRACK2) rather than "known first, then new".
    with StateDB(pipeline["state_db_path"]) as db:
        db.record_track(
            TrackRecord(
                source="spotify",
                source_id="song-2",
                local_path=str(pipeline["library_root"] / "Artist Two" / "Album Two" / "Existing.mp3"),
                title="Track Two",
                artist="Artist Two",
                downloaded_at="2026-01-01T00:00:00+00:00",
            )
        )

    result = _fetch(pipeline)

    assert len(result.new_tracks) == 1
    assert result.new_tracks[0].source_id == "song-1"
    assert len(result.already_known_tracks) == 1

    lines = result.m3u8_path.read_text().splitlines()[1:]
    assert lines[0].endswith("01 Track One.mp3")  # newly downloaded TRACK1
    assert lines[1].endswith("Existing.mp3")  # pre-known TRACK2, in position 2


def test_fetch_playlist_disambiguates_colliding_filenames(monkeypatch, pipeline):
    # Two distinct tracks that happen to produce the same destination path
    # (same artist/album/track_number/title) must not have the second
    # download silently overwrite the first.
    collider = TrackMeta(
        source_id="song-2",
        title="Track One",
        artist="Artist One",
        album="Album One",
        track_number=1,
    )
    monkeypatch.setattr(
        download_module,
        "get_playlist_tracks",
        lambda credentials_path, source_id: [TRACK1, collider],
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_zotify_run({"song-1": "track1.mp3", "song-2": "track2.mp3"}),
    )

    result = _fetch(pipeline)

    assert len(result.new_tracks) == 2
    paths = {r.source_id: Path(r.local_path) for r in result.new_tracks}
    assert paths["song-1"] != paths["song-2"]
    assert paths["song-1"].exists()
    assert paths["song-2"].exists()
    for source_id, path in paths.items():
        assert read_dedup_tags(path)[1] == source_id


def test_fetch_playlist_handles_unmatched_track_gracefully(monkeypatch, pipeline):
    monkeypatch.setattr(
        subprocess,
        "run",
        _fake_zotify_run({"song-1": "track1.mp3"}),  # song-2 "fails"
    )

    result = _fetch(pipeline)

    assert len(result.new_tracks) == 1
    assert result.new_tracks[0].source_id == "song-1"
    assert len(result.unmatched_tracks) == 1
    assert result.unmatched_tracks[0].source_id == "song-2"

    lines = result.m3u8_path.read_text().splitlines()
    assert len(lines) == 2  # header + only the successfully downloaded track
