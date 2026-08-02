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


_MAX_SPOKEN_WORD_LOSSY_KBPS = 96
"""Ceiling for a lossy AAC re-encode of spoken-word audio. Not a hardware
limit -- the iPod's AAC decoder handles up to 320kbps same as music --
but matches iOpenPod's own "Spoken Word Bitrate" setting range (32-96kbps,
gui/widgets/settingsPage.py), the established convention in this device's
own sync tooling for where dialogue-only content stops benefiting from a
higher bitrate. Source audio at or under this is re-encoded lossy at
~its own bitrate (never boosted above it -- no benefit). Source audio
above it is re-encoded lossless (ALAC) instead of forced down to this
ceiling, so a second lossy encoding generation isn't stacked on top of
the first. Fixes a real regression: Kafka's original 96kbps MP3 got
knocked down to a hardcoded 64k AAC by the old flat default -- see
notes.md."""

_MIN_SPOKEN_WORD_LOSSY_KBPS = 32


def probe_bitrate_kbps(ffprobe_path: str, path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "error",
            "-show_entries", "format=bit_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise MergeError(f"ffprobe failed on {path}: {result.stderr}")
    return float(result.stdout.strip()) / 1000


def select_encoding(parts: list[Path], ffprobe_path: str) -> tuple[str, str | None]:
    """(codec, bitrate_flag) for the auto (no explicit --bitrate) path.
    bitrate_flag is None for the lossless case -- ALAC doesn't take -b:a.

    Rounds the probed source bitrate before comparing to the cap --
    ffprobe's format-level bit_rate includes container/frame overhead, so
    a source nominally *at* the cap (e.g. a real 96kbps MP3 probing at
    96.005-96.006kbps) shouldn't trip the lossless branch: there's no
    extra information in that overhead to preserve, only ~16x the
    storage cost for a multi-hour book. Confirmed live re-encoding
    Kafka's real 96kbps source (see notes.md)."""
    source_kbps = round(max(probe_bitrate_kbps(ffprobe_path, p) for p in parts))
    if source_kbps > _MAX_SPOKEN_WORD_LOSSY_KBPS:
        return "alac", None
    target_kbps = max(source_kbps, _MIN_SPOKEN_WORD_LOSSY_KBPS)
    return "aac", f"{target_kbps}k"


def _concat_escape(path: Path) -> str:
    """Escape a path for ffmpeg's concat-demuxer list format. The format
    quotes each path in single quotes with no other escaping mechanism,
    so a literal single quote inside the path (e.g. a chapter file named
    "...Publisher's Introduction.mp3") must be closed out, escaped, and
    reopened -- '\\'' -- or ffmpeg silently truncates the path at that
    quote and fails to open it. Confirmed live on Neil Postman's Amusing
    Ourselves to Death (see notes.md)."""
    return str(path).replace("'", "'\\''")


_PART_SUFFIXES = {".mp3", ".m4a"}


def discover_parts(parts_dir: Path | str) -> list[Path]:
    """Sorted *.mp3/*.m4a files directly under parts_dir. Zero-padded
    names (01.mp3..12.mp3) sort correctly lexicographically, no
    natural-sort library needed -- caller is responsible for staging
    non-zero-padded or multi-disc source dumps into that shape first.
    Raises MergeError if none found."""
    parts_dir = Path(parts_dir)
    parts = sorted(p for p in parts_dir.iterdir() if p.suffix.lower() in _PART_SUFFIXES)
    if not parts:
        raise MergeError(f"no .mp3/.m4a files found in {parts_dir}")
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
    bitrate: str | None = None,
    title: str | None = None,
) -> Path:
    """Concats every .mp3 part under parts_dir into one .m4b at
    output_path, with one chapter per source part. `title` seeds the
    output's own title tag -- defaults to derive_title_from_folder_name
    of parts_dir's name if not given. Returns the resolved output_path.

    `bitrate` is normally left as None: the codec and bitrate are then
    auto-selected per select_encoding's source-matching/lossless-cutover
    policy. Pass an explicit value (e.g. "64k") only to force a flat
    lossy AAC bitrate regardless of source, overriding that policy."""
    ffmpeg_path = find_ffmpeg()
    ffprobe_path = find_ffprobe()
    parts_dir = Path(parts_dir).resolve()
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if title is None:
        title = derive_title_from_folder_name(parts_dir.name)

    parts = discover_parts(parts_dir)
    durations = [probe_duration_seconds(ffprobe_path, p) for p in parts]

    if bitrate is not None:
        codec, bitrate_flag = "aac", bitrate
    else:
        codec, bitrate_flag = select_encoding(parts, ffprobe_path)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        list_path = tmp_path / "parts.txt"
        list_path.write_text(
            "\n".join(f"file '{_concat_escape(p)}'" for p in parts) + "\n"
        )
        meta_path = tmp_path / "chapters.txt"
        meta_path.write_text(build_ffmetadata(list(zip(parts, durations)), title=title))

        cmd = [
            ffmpeg_path,
            "-y",
            "-v", "error",
            "-f", "concat", "-safe", "0", "-i", str(list_path),
            "-f", "ffmetadata", "-i", str(meta_path),
            "-map", "0:a",
            "-map_metadata", "1",
            "-map_chapters", "1",
            "-c:a", codec,
        ]
        if bitrate_flag is not None:
            cmd += ["-b:a", bitrate_flag]
        cmd += ["-movflags", "+faststart", str(output_path)]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise MergeError(f"ffmpeg exited {result.returncode}\n{result.stderr}")

    return output_path
