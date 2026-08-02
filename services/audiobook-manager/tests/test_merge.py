from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from audiobook_manager.merge import (
    MergeError,
    build_ffmetadata,
    derive_title_from_folder_name,
    discover_parts,
    find_ffmpeg,
    find_ffprobe,
    merge_parts_to_m4b,
    probe_bitrate_kbps,
    select_encoding,
)
from audiobook_manager.merge import _concat_escape

FIXTURES = Path(__file__).parent / "fixtures"


def _make_synthetic_mp3(path: Path, *, bitrate_kbps: int, channels: int = 2) -> Path:
    """1-second sine-wave MP3 at a specific bitrate/channel count, for
    exercising select_encoding's cutover without needing checked-in
    high-bitrate fixture audio."""
    subprocess.run(
        [
            find_ffmpeg(),
            "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-ac", str(channels),
            "-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def test_discover_parts_sorts_and_filters(tmp_path: Path) -> None:
    (tmp_path / "02.mp3").write_bytes(b"")
    (tmp_path / "01.mp3").write_bytes(b"")
    (tmp_path / "10.mp3").write_bytes(b"")
    (tmp_path / "notes.txt").write_bytes(b"")

    parts = discover_parts(tmp_path)

    assert [p.name for p in parts] == ["01.mp3", "02.mp3", "10.mp3"]


def test_discover_parts_raises_when_empty(tmp_path: Path) -> None:
    with pytest.raises(MergeError):
        discover_parts(tmp_path)


def test_discover_parts_accepts_m4a(tmp_path: Path) -> None:
    (tmp_path / "02.m4a").write_bytes(b"")
    (tmp_path / "01.m4a").write_bytes(b"")

    parts = discover_parts(tmp_path)

    assert [p.name for p in parts] == ["01.m4a", "02.m4a"]


def test_build_ffmetadata_chapter_boundaries() -> None:
    text = build_ffmetadata(
        [(Path("01.mp3"), 2.5), (Path("02.mp3"), 3.0)], title="The Trial"
    )

    assert text.startswith(";FFMETADATA1")
    assert "title=The Trial" in text
    assert "START=0" in text
    assert "END=2500" in text
    assert "START=2500" in text
    assert "END=5500" in text
    assert "title=01" in text
    assert "title=02" in text


def test_derive_title_from_folder_name_splits_author_and_title() -> None:
    assert derive_title_from_folder_name("Franz Kafka - The Trial") == "The Trial"


def test_derive_title_from_folder_name_falls_back_to_whole_name() -> None:
    assert derive_title_from_folder_name("The Trial") == "The Trial"


def test_merge_parts_to_m4b_end_to_end(tmp_path: Path) -> None:
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    for name in ("part_01.mp3", "part_02.mp3", "part_03.mp3"):
        shutil.copy(FIXTURES / name, parts_dir / name)

    output = merge_parts_to_m4b(parts_dir, tmp_path / "out.m4b", bitrate="32k")

    assert output.is_file()

    ffprobe = shutil.which("ffprobe")
    assert ffprobe

    tags = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format_tags=title",
         "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert tags.stdout.strip() == "parts"

    chapters = subprocess.run(
        [ffprobe, "-v", "error", "-show_chapters", "-of", "csv=p=0", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    chapter_lines = [line for line in chapters.stdout.strip().splitlines() if line]
    assert len(chapter_lines) == 3

    duration = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(output)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert 5.5 <= float(duration.stdout.strip()) <= 6.5


def test_probe_bitrate_kbps_reads_source_bitrate() -> None:
    # Fixtures are ~27kbps mono MP3 -- see select_encoding tests below.
    kbps = probe_bitrate_kbps(find_ffprobe(), FIXTURES / "part_01.mp3")
    assert 20 <= kbps <= 35


def test_select_encoding_matches_low_bitrate_source(tmp_path: Path) -> None:
    # Real source bitrate (~27k) is below the 32k floor -- floor wins.
    codec, bitrate_flag = select_encoding([FIXTURES / "part_01.mp3"], find_ffprobe())
    assert codec == "aac"
    assert bitrate_flag == "32k"


def test_select_encoding_matches_mid_range_source_bitrate(tmp_path: Path) -> None:
    # libmp3lame's actual output bitrate overshoots a CBR target slightly
    # (container/frame overhead) -- assert the ballpark, not an exact match.
    part = _make_synthetic_mp3(tmp_path / "part.mp3", bitrate_kbps=64)
    codec, bitrate_flag = select_encoding([part], find_ffprobe())
    assert codec == "aac"
    assert bitrate_flag is not None
    assert 60 <= int(bitrate_flag.rstrip("k")) <= 75


def test_select_encoding_falls_back_to_lossless_above_spoken_word_cap(
    tmp_path: Path,
) -> None:
    # Above the 96kbps spoken-word ceiling -- go lossless (ALAC) instead
    # of forcing a second lossy generation on top of the source.
    part = _make_synthetic_mp3(tmp_path / "part.mp3", bitrate_kbps=128)
    codec, bitrate_flag = select_encoding([part], find_ffprobe())
    assert codec == "alac"
    assert bitrate_flag is None


def test_merge_parts_to_m4b_auto_selects_lossy_bitrate_matching_low_bitrate_source(
    tmp_path: Path,
) -> None:
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    for name in ("part_01.mp3", "part_02.mp3", "part_03.mp3"):
        shutil.copy(FIXTURES / name, parts_dir / name)

    output = merge_parts_to_m4b(parts_dir, tmp_path / "out.m4b")

    probe = subprocess.run(
        [find_ffprobe(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of",
         "default=noprint_wrappers=1:nokey=1", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "aac"


def test_concat_escape_handles_embedded_single_quote() -> None:
    assert _concat_escape(Path("/x/Publisher's Introduction.mp3")) == (
        "/x/Publisher'\\''s Introduction.mp3"
    )


def test_merge_parts_to_m4b_handles_apostrophe_in_part_filename(tmp_path: Path) -> None:
    # Regression: ffmpeg's concat-list format truncates an unescaped
    # single quote mid-path -- confirmed live on a real "Publisher's
    # Introduction.mp3" source file (see notes.md).
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    shutil.copy(FIXTURES / "part_01.mp3", parts_dir / "01 - Publisher's Introduction.mp3")
    shutil.copy(FIXTURES / "part_02.mp3", parts_dir / "02.mp3")

    output = merge_parts_to_m4b(parts_dir, tmp_path / "out.m4b", bitrate="32k")

    assert output.is_file()
    chapters = subprocess.run(
        [find_ffprobe(), "-v", "error", "-show_chapters", "-of", "csv=p=0", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert len([line for line in chapters.stdout.strip().splitlines() if line]) == 2


def test_merge_parts_to_m4b_auto_selects_lossless_when_source_exceeds_cap(
    tmp_path: Path,
) -> None:
    parts_dir = tmp_path / "parts"
    parts_dir.mkdir()
    _make_synthetic_mp3(parts_dir / "01.mp3", bitrate_kbps=128)
    _make_synthetic_mp3(parts_dir / "02.mp3", bitrate_kbps=128)

    output = merge_parts_to_m4b(parts_dir, tmp_path / "out.m4b")

    probe = subprocess.run(
        [find_ffprobe(), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of",
         "default=noprint_wrappers=1:nokey=1", str(output)],
        capture_output=True, text=True, check=True,
    )
    assert probe.stdout.strip() == "alac"
