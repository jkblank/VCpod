from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4

_SKIP_DIR_NAMES = {".duplicates"}
_SCRATCH_DIR_RE = re.compile(r"^_.*_scratch$")
_AUDIO_SUFFIXES = {".m4a", ".mp3"}


@dataclass
class TrackInfo:
    path: Path
    source: str
    source_id: str
    title: str
    artist: str
    isrc: str | None


def _mp4_freeform(audio: MP4, key: str) -> str | None:
    values = audio.get(key)
    if not values:
        return None
    return bytes(values[0]).decode("utf-8")


def _parse_xid_isrc(values: list | None) -> str | None:
    # gamdl writes the ISRC into the standard `xid ` atom as
    # "{rights_holder_label}:isrc:{ISRC}".
    if not values:
        return None
    parts = str(values[0]).split(":isrc:")
    return parts[1] if len(parts) == 2 and parts[1] else None


def _read_m4a(path: Path) -> TrackInfo | None:
    audio = MP4(path)
    source = _mp4_freeform(audio, "----:com.apple.iTunes:source")
    source_id = _mp4_freeform(audio, "----:com.apple.iTunes:source_id")
    if not source or not source_id:
        return None
    title = str((audio.get("\xa9nam") or [""])[0])
    artist = str((audio.get("\xa9ART") or [""])[0])
    isrc = _parse_xid_isrc(audio.get("xid "))
    return TrackInfo(
        path=path, source=source, source_id=source_id, title=title, artist=artist, isrc=isrc
    )


def _id3_txxx(tags: ID3, desc: str) -> str | None:
    frames = tags.getall(f"TXXX:{desc}")
    if not frames:
        return None
    return str(frames[0].text[0])


def _id3_text(tags: ID3, frame_id: str) -> str | None:
    frame = tags.get(frame_id)
    if not frame or not frame.text:
        return None
    return str(frame.text[0])


def _read_mp3(path: Path) -> TrackInfo | None:
    try:
        tags = ID3(path)
    except ID3NoHeaderError:
        return None
    source = _id3_txxx(tags, "source")
    source_id = _id3_txxx(tags, "source_id")
    if not source or not source_id:
        return None
    title = _id3_text(tags, "TIT2") or ""
    artist = _id3_text(tags, "TPE1") or ""
    isrc = _id3_text(tags, "TSRC")
    return TrackInfo(
        path=path, source=source, source_id=source_id, title=title, artist=artist, isrc=isrc
    )


def read_track_info(path: Path | str) -> TrackInfo | None:
    """Reads our own dedup-relevant tags from a downloaded audio file.
    Returns None (never raises) for anything that isn't one of ours — e.g.
    a file a user dropped into the library manually, or one with no tags
    at all."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".m4a":
        return _read_m4a(path)
    if suffix == ".mp3":
        return _read_mp3(path)
    return None


def _is_skipped(path: Path, library_root: Path) -> bool:
    relative_parts = path.relative_to(library_root).parts[:-1]
    return any(part in _SKIP_DIR_NAMES or _SCRATCH_DIR_RE.match(part) for part in relative_parts)


def scan_library(library_root: Path | str) -> list[TrackInfo]:
    library_root = Path(library_root)
    tracks: list[TrackInfo] = []
    for path in sorted(library_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
            continue
        if _is_skipped(path, library_root):
            continue
        info = read_track_info(path)
        if info is not None:
            tracks.append(info)
    return tracks
