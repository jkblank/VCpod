from pathlib import Path

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
