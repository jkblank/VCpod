import shutil
from pathlib import Path

from library_manager.scan import read_track_info, scan_library

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_read_track_info_m4a():
    info = read_track_info(FIXTURES / "tagged.m4a")
    assert info is not None
    assert info.source == "apple_music"
    assert info.source_id == "111111111"
    assert info.title == "Fixture Title"
    assert info.artist == "Fixture Artist"
    assert info.isrc == "USTEST0000001"


def test_read_track_info_mp3():
    info = read_track_info(FIXTURES / "tagged.mp3")
    assert info is not None
    assert info.source == "spotify"
    assert info.source_id == "222222222"
    assert info.isrc == "USTEST0000001"


def test_read_track_info_returns_none_for_untagged_file():
    assert read_track_info(FIXTURES / "untagged.m4a") is None


def test_read_track_info_returns_none_for_unsupported_extension(tmp_path: Path):
    other = tmp_path / "notes.txt"
    other.write_text("hello")
    assert read_track_info(other) is None


def test_scan_library_skips_duplicates_and_scratch_dirs(tmp_path: Path):
    library_root = tmp_path / "music"
    (library_root / "Artist" / "Album").mkdir(parents=True)
    shutil.copy(FIXTURES / "tagged.m4a", library_root / "Artist" / "Album" / "01 Song.m4a")

    (library_root / ".duplicates" / "apple_music").mkdir(parents=True)
    shutil.copy(
        FIXTURES / "tagged.mp3", library_root / ".duplicates" / "apple_music" / "ignored.mp3"
    )

    (library_root / "_gamdl_scratch" / "pl.xxx").mkdir(parents=True)
    shutil.copy(
        FIXTURES / "tagged.mp3", library_root / "_gamdl_scratch" / "pl.xxx" / "ignored2.mp3"
    )

    tracks = scan_library(library_root)

    assert len(tracks) == 1
    assert tracks[0].source_id == "111111111"
