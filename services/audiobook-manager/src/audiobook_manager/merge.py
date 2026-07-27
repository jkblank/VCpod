from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


class MergeError(Exception):
    pass


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise MergeError("ffmpeg not found on PATH")
    return path


def find_ffprobe() -> str:
    path = shutil.which("ffprobe")
    if not path:
        raise MergeError("ffprobe not found on PATH")
    return path


def discover_parts(parts_dir: Path | str) -> list[Path]:
    """Sorted *.mp3 files directly under parts_dir. Zero-padded names
    (01.mp3..12.mp3) sort correctly lexicographically, no natural-sort
    library needed. Raises MergeError if none found."""
    parts_dir = Path(parts_dir)
    parts = sorted(p for p in parts_dir.iterdir() if p.suffix.lower() == ".mp3")
    if not parts:
        raise MergeError(f"no .mp3 files found in {parts_dir}")
    return parts


def probe_duration_seconds(ffprobe_path: str, path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MergeError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip())


def derive_title_from_folder_name(name: str) -> str:
    """"Author - Title" -> "Title"; falls back to the whole name if no
    " - " separator is present.

    Only an initial/fallback title -- beets-audible's own Audible lookup
    may override it on a confident match, but it is copied through
    unchanged on an ambiguous match or a skip (confirmed live: without
    this, the merged file's own filename-derived tag -- literally
    "merged", since that's the staging filename -- ends up as the
    permanent track title). See notes.md."""
    _, sep, rest = name.partition(" - ")
    return rest.strip() if sep and rest.strip() else name


def build_ffmetadata(parts_with_durations: list[tuple[Path, float]], *, title: str) -> str:
    """One [CHAPTER] block per source part (boundaries from cumulative
    durations), preceded by a global `title=` tag -- real tagging
    (author/album/etc.) happens later via beets-audible, but title is
    seeded here since beets-audible doesn't reliably override it (see
    derive_title_from_folder_name)."""
    lines = [";FFMETADATA1", f"title={title}", ""]
    start_ms = 0
    for path, duration in parts_with_durations:
        end_ms = start_ms + round(duration * 1000)
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start_ms}",
            f"END={end_ms}",
            f"title={path.stem}",
            "",
        ]
        start_ms = end_ms
    return "\n".join(lines)


def merge_parts_to_m4b(
    parts_dir: Path | str,
    output_path: Path | str,
    *,
    bitrate: str = "64k",
    title: str | None = None,
) -> Path:
    """Concats every .mp3 part under parts_dir into one AAC-encoded .m4b
    at output_path, with one chapter per source part. `title` seeds the
    output's own title tag -- defaults to derive_title_from_folder_name
    of parts_dir's name if not given. Returns the resolved output_path."""
    ffmpeg_path = find_ffmpeg()
    ffprobe_path = find_ffprobe()
    parts_dir = Path(parts_dir).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if title is None:
        title = derive_title_from_folder_name(parts_dir.name)

    parts = discover_parts(parts_dir)
    durations = [probe_duration_seconds(ffprobe_path, p) for p in parts]

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        list_path = tmp_path / "parts.txt"
        list_path.write_text(
            "\n".join(f"file '{p}'" for p in parts) + "\n"
        )
        meta_path = tmp_path / "chapters.txt"
        meta_path.write_text(build_ffmetadata(list(zip(parts, durations)), title=title))

        result = subprocess.run(
            [
                ffmpeg_path,
                "-y",
                "-v", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-f", "ffmetadata", "-i", str(meta_path),
                "-map", "0:a",
                "-map_metadata", "1",
                "-map_chapters", "1",
                "-c:a", "aac",
                "-b:a", bitrate,
                "-movflags", "+faststart",
                str(output_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MergeError(f"ffmpeg exited {result.returncode}\n{result.stderr}")

    return output_path
