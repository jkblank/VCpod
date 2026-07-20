from pathlib import Path

import pytest

from common.playlist import write_m3u8


def test_write_m3u8_creates_parent_dirs_and_header(tmp_path: Path):
    target = tmp_path / "playlists" / "john" / "ALT CTRL.m3u8"
    write_m3u8(target, ["/library/music/A/B/01 Song.m4a", "/library/music/C/D/02 Song.m4a"])

    assert target.is_file()
    lines = target.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1:] == [
        "/library/music/A/B/01 Song.m4a",
        "/library/music/C/D/02 Song.m4a",
    ]


def test_write_m3u8_empty_track_list(tmp_path: Path):
    target = tmp_path / "empty.m3u8"
    write_m3u8(target, [])
    assert target.read_text() == "#EXTM3U\n"


def test_write_m3u8_absolute_mode_replaces_contents(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"])

    # Source playlist dropped the first track and added a new one — an
    # "absolute" rewrite should mirror that exactly, including the removal.
    write_m3u8(target, ["/library/music/B/02 Song.m4a", "/library/music/C/03 Song.m4a"], mode="absolute")

    lines = target.read_text().splitlines()
    assert lines[1:] == [
        "/library/music/B/02 Song.m4a",
        "/library/music/C/03 Song.m4a",
    ]


def test_write_m3u8_additive_mode_preserves_existing_entries(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"])

    # Source playlist dropped the first track and added a new one — an
    # "additive" rewrite must keep the dropped track too, only adding the
    # genuinely new one.
    write_m3u8(target, ["/library/music/B/02 Song.m4a", "/library/music/C/03 Song.m4a"], mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == [
        "/library/music/A/01 Song.m4a",
        "/library/music/B/02 Song.m4a",
        "/library/music/C/03 Song.m4a",
    ]


def test_write_m3u8_additive_mode_no_duplicate_when_rerun_unchanged(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    tracks = ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"]
    write_m3u8(target, tracks, mode="additive")
    write_m3u8(target, tracks, mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == tracks


def test_write_m3u8_additive_mode_on_nonexistent_file_behaves_like_absolute(tmp_path: Path):
    target = tmp_path / "new_playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a"], mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == ["/library/music/A/01 Song.m4a"]


def test_write_m3u8_unknown_mode_raises(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    with pytest.raises(ValueError):
        write_m3u8(target, ["/library/music/A/01 Song.m4a"], mode="bogus")
