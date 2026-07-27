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
    merge_parts_to_m4b,
)

FIXTURES = Path(__file__).parent / "fixtures"


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
