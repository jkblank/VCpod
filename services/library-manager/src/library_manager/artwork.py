"""Normalizes embedded cover art by decoding + re-encoding it through
Pillow, unconditionally for any track whose art shows a marker Pillow's
own default JPEG encoder never produces.

Found live (2026-08-25): a real track ("Cool Kids" by Echosmith) whose
embedded JPEG cover art carries a DRI (Define Restart Interval) marker
plus an APP13/Photoshop segment — consistent with having been processed
through Photoshop or similar tooling — reliably broke on-device album
art rendering on a real 6th Gen iPod Classic test unit, confirmed
independent of total library size/track count and independent of image
dimensions (a same-size re-encode fixed it just as well as a resize did,
isolating the fix to "re-encode" rather than "shrink"). The DRI marker
alone isn't a reliable predictor of which tracks are affected — 94.5%
of the real library carries it, most working fine — so there was no
cheap way found to tell "affected" apart from "shares a marker, but
fine" short of per-track device testing. This re-encodes every track
whose art shows any of a handful of non-Pillow-default JPEG markers
rather than guessing further; see notes.md, 2026-08-25.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

from mutagen.id3 import ID3, APIC, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

logger = logging.getLogger(__name__)

_JPEG_QUALITY = 90
_MP4_ART_SUFFIXES = {".m4a", ".m4b"}
_ID3_ART_SUFFIXES = {".mp3"}
_AUDIO_SUFFIXES = _MP4_ART_SUFFIXES | _ID3_ART_SUFFIXES

# Marker IDs that Pillow's own default JPEG encoder never writes.
# 0xDD DRI  (Define Restart Interval)
# 0xED APP13 (Photoshop IRB)
# 0xEE APP14 (Adobe)
# 0xC2 SOF2 (progressive)
_NON_DEFAULT_MARKERS = frozenset({0xDD, 0xED, 0xEE, 0xC2})
_NO_LENGTH_MARKERS = frozenset({0xD8, 0xD9, 0x01}) | frozenset(range(0xD0, 0xD8))


def _jpeg_needs_reencode(data: bytes) -> bool:
    """Walks JPEG markers up to the start-of-scan, without decoding pixel
    data, looking for any marker Pillow's default encoder never writes.
    Returns False (leave untouched) for anything that doesn't parse as a
    well-formed marker sequence up to that point — an actually-corrupt
    file is caught later, at decode time, in normalize_track_artwork."""
    if data[:2] != b"\xff\xd8":
        return False
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            return False
        marker = data[i + 1]
        if marker in _NO_LENGTH_MARKERS:
            i += 2
            continue
        if i + 4 > len(data):
            return False
        if marker in _NON_DEFAULT_MARKERS:
            return True
        if marker == 0xDA:  # start of scan -- entropy-coded data follows
            return False
        length = (data[i + 2] << 8) | data[i + 3]
        i += 2 + length
    return False


def _reencode_jpeg(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data))
    img.load()
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def _normalize_mp4_artwork(path: Path, *, dry_run: bool) -> bool:
    mp4 = MP4(path)
    tags = mp4.tags
    covr = tags.get("covr") if tags else None
    if not covr:
        return False
    original = covr[0]
    data = bytes(original)
    if original.imageformat != MP4Cover.FORMAT_JPEG or not _jpeg_needs_reencode(data):
        return False
    if dry_run:
        return True
    new_data = _reencode_jpeg(data)
    tags["covr"] = [MP4Cover(new_data, imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()
    return True


def _normalize_id3_artwork(path: Path, *, dry_run: bool) -> bool:
    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        return False
    apics = id3.getall("APIC")
    if not any(apic.mime == "image/jpeg" and _jpeg_needs_reencode(apic.data) for apic in apics):
        return False
    if dry_run:
        return True
    new_apics = []
    for apic in apics:
        if apic.mime != "image/jpeg" or not _jpeg_needs_reencode(apic.data):
            new_apics.append(apic)
            continue
        new_apics.append(
            APIC(
                encoding=apic.encoding,
                mime="image/jpeg",
                type=apic.type,
                desc=apic.desc,
                data=_reencode_jpeg(apic.data),
            )
        )
    id3.delall("APIC")
    for apic in new_apics:
        id3.add(apic)
    id3.save(path, v2_version=3)
    return True


def normalize_track_artwork(path: Path | str, *, dry_run: bool = False) -> bool:
    """Re-encodes path's embedded cover art through Pillow if it carries
    any marker not produced by Pillow's own default JPEG encoder.
    Returns whether the file was (or, with dry_run, would be) changed.
    No-ops for tracks with no embedded art, non-JPEG art (already not
    what triggered this), or art that's already clean. Self-limiting on
    repeat runs: after one real re-encode none of those markers remain,
    so later passes skip it without decoding — safe to re-run over the
    whole library on every tick without repeated lossy recompression or
    wasted work."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in _MP4_ART_SUFFIXES:
        return _normalize_mp4_artwork(path, dry_run=dry_run)
    if suffix in _ID3_ART_SUFFIXES:
        return _normalize_id3_artwork(path, dry_run=dry_run)
    return False


@dataclass
class ArtworkNormalizeResult:
    scanned: int = 0
    normalized: list[Path] = field(default_factory=list)
    failures: list[tuple[Path, str]] = field(default_factory=list)


def normalize_library_artwork(
    library_root: Path | str, *, dry_run: bool = False
) -> ArtworkNormalizeResult:
    """Walks library_root for every .m4a/.m4b/.mp3 file and normalizes
    its embedded artwork (or, with dry_run, reports what would change
    without writing anything). A per-track failure (corrupt file, decode
    error) is caught and reported in `failures`, not raised — matches
    this project's existing "one bad item doesn't abort the whole batch"
    convention (dedup groups, fetch outcomes, podcast sync)."""
    library_root = Path(library_root)
    result = ArtworkNormalizeResult()
    for path in sorted(library_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _AUDIO_SUFFIXES:
            continue
        result.scanned += 1
        try:
            if normalize_track_artwork(path, dry_run=dry_run):
                result.normalized.append(path)
        except Exception as e:
            logger.warning("could not normalize artwork for %s: %s", path, e)
            result.failures.append((path, str(e)))
    return result
