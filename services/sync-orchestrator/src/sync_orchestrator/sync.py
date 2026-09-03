"""Core sync logic: builds and optionally executes a sync plan against a
connected iPod, driven by profile config + explicit library/state roots
rather than hardcoded paths.

library_root/state_root are taken as explicit arguments rather than read
from global.yaml's `paths` — those are Docker-container paths
(/data/library, /data/state, per docker-compose.yml's volume mounts) and
this service always runs bare metal (confirmed live: global.yaml's paths
don't exist on the host at all). Matches the same explicit
--library-root/--state-path pattern already used by fetcher-apple and
podcast-manager, rather than introducing a new, inconsistent way to
resolve these paths.

Ported from the M6 spike (formerly
services/ipod-sync/spike/headless_write_poc.py, now retired in favor of
this real service). Every workaround here is explained in full in
docs/m6-ipod-headless-recommendation.md and notes.md — this module keeps
the reasoning terse and points there instead of repeating it.
"""

from __future__ import annotations

import dataclasses
import email.utils
import logging
import re
import shutil
import sqlite3
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import iopenpod.device as _iopenpod_device
from common.models import ProfileConfig
from common.state import StateDB
from iopenpod.artworkdb_writer import artwork_writer as _artwork_writer
from iopenpod.artworkdb_writer import artworkdb_chunks as _artworkdb_chunks

# iopenpod rolls each artwork format over to a new F{format}_N.ithmb file
# once the current one passes 32MB (its own ITHMB_MAX_SIZE_BYTES) -- a
# budget unrelated to any real device/filesystem limit (iopenpod tracks
# the actual FAT32 per-file ceiling separately, in
# device/filesystem_profile.py's _MAX_FILE_SIZE_BYTES, but never threads
# it into this writer). Confirmed live (2026-08-26) on the 6th Gen
# testbed: real Apple iTunes wrote a single 335MB F1060_1.ithmb with no
# rollover and rendered fine; iopenpod's own 32MB-chunked write produced
# total on-device artwork failure past ~1,818-1,958 tracks even though
# that's a *smaller* total byte count. Re-synced the same testbed at
# 1,984 tracks with this raised to FAT32's real per-file ceiling (same
# number iopenpod's own filesystem_profile.py already uses) instead of
# 32MB, confirmed art renders. See notes.md.
#
# TEMPORARY WORKAROUND: upstreamed as TheRealSavi/iOpenPod#186. Once that
# (or an equivalent fix) merges and iopenpod's own pinned version here is
# bumped past it, delete this monkeypatch entirely (import above included)
# and the now-redundant regression test in test_sync.py -- see notes.md's
# "album-art size ceiling" entry for the full removal steps.
_artwork_writer.ITHMB_MAX_SIZE_BYTES = 4 * 1024**3 - 1  # FAT32 max file size
from iopenpod.device.info import DeviceInfo, resolve_itdb_path
from iopenpod.itunesdb_writer import mhlt_writer as _mhlt_writer
from iopenpod.itunesdb_parser.ipod_library import load_ipod_library
from iopenpod.podcasts.models import PodcastEpisode, PodcastFeed
from iopenpod.podcasts.podcast_sync import build_podcast_sync_plan
from iopenpod.sync.audio_fingerprint import FingerprintCache
from iopenpod.sync.backup_manager import BackupManager, BackupProgress, SnapshotInfo
from iopenpod.sync.core.engine import SyncEngine
from iopenpod.sync.core.models import (
    EngineOperation,
    EngineOptions,
    EngineProgress,
    EngineRequest,
)
from iopenpod.sync.mapping import MappingManager
from iopenpod.sync.transcoder import TranscodeOptions

from sync_orchestrator.playstate import resolve_played_states
from sync_orchestrator.podcast_artwork_backfill import (
    build_podcast_artwork_backfill_items,
    exclude_conflicting_with_removal,
)
from sync_orchestrator.podcast_removal import build_podcast_removal_items
from sync_orchestrator.selection import (
    build_media_folders,
    build_staging_dir,
    resolve_audiobooks_folder,
    resolve_music_folder,
    resolve_selected_files,
)


logger = logging.getLogger(__name__)


class SyncError(Exception):
    pass


# iopenpod's DeviceInfo.enrich() resolves artwork_formats from a static,
# hardcoded per-family table (device/capabilities.py's
# CLASSIC_COVER_ART_FORMATS for the whole "iPod Classic" family) *before*
# it ever tries reading the device's own SysInfoExtended, and short-
# circuits on the first non-empty result — so the device's real,
# authoritative format list is never even consulted for any Classic-family
# unit. Confirmed live (2026-08-17) against a real "iPod Classic" 7th Gen
# (MC293): its SysInfoExtended `AlbumArt` array declares 5 formats, and
# format 1061 is 55x55 vs the static table's 56x56.
#
# Confirmed live (2026-08-18), byte-diffing a real iTunes-authored
# ArtworkDB pulled straight off this exact device against ours: iTunes
# only ever writes 3 of those 5 formats per track — 1055 (128x128), 1060
# (320x320), 1061 (55x55) — every single one of 7/7 real entries checked,
# no exceptions. The other 2 (1068, 1069) are declared in SysInfoExtended
# but never appear in any real per-track mhii entry. The plist itself
# explains why: 1055/1060/1061 all have `AssociatedFormat=0`, while 1068
# has `AssociatedFormat=2` and 1069 has `AssociatedFormat=131072` +
# `ExcludedFormats=-1` — i.e. 1068/1069 are reserved for some other,
# non-track-artwork purpose (Now Playing chrome, video thumbnails,
# whatever `AssociatedFormat=2`/`131072` denote), not part of the normal
# per-track thumbnail set. Passing all 5 straight through (as this
# function used to) makes `_required_device_format_ids()`
# (artworkdb_writer/artwork_writer.py) treat all 5 as *required* for
# every entry — writing 2 extra mhod containers real iTunes never
# writes, corrupting the on-device entry shape in the exact same spirit
# as the missing-mhod6-chunk bug below, just an addition instead of an
# omission. `_read_device_album_art_formats()` below now filters to
# `AssociatedFormat == 0` entries only, matching what real iTunes
# actually writes. Every other layer (iTunesDB<->ArtworkDB link, the
# mhii missing-chunk workaround below, raw pixel bytes) was
# independently byte-diffed and confirmed correct — see notes.md.
#
# Apple's own SysInfoExtended XML is invalid plist (`<key>` elements
# directly inside an `<array>`, one per format, immediately before each
# format's `<dict>`) — confirmed live: `plistlib.loads()` raises
# "unexpected key" on the real file, so iopenpod's own regex fallback
# parser runs instead, which doesn't attempt to extract nested
# array-of-dicts structures like `AlbumArt` at all and silently returns
# nothing for it. Rather than patch iopenpod's general-purpose plist
# parser to tolerate Apple's non-standard shape, this strips just the
# offending `<key>` elements before parsing so plistlib succeeds, then
# reuses iopenpod's own `extract_image_formats()` for the actual
# dimension-field lookup, keeping this workaround minimal.
_SYSINFO_EXTENDED_ARRAY_KEY_RE = re.compile(rb"<array>(.*?)</array>", re.DOTALL)
_SYSINFO_EXTENDED_ARRAY_ITEM_KEY_RE = re.compile(rb"<key>[^<]*</key>\s*(?=<dict>)")


def _sanitize_sysinfo_extended_plist(raw: bytes) -> bytes:
    def _strip_keys_in_array(match: re.Match[bytes]) -> bytes:
        body = _SYSINFO_EXTENDED_ARRAY_ITEM_KEY_RE.sub(b"", match.group(1))
        return b"<array>" + body + b"</array>"

    return _SYSINFO_EXTENDED_ARRAY_KEY_RE.sub(_strip_keys_in_array, raw)


def _filter_to_plain_album_art_formats(
    plist: dict[str, Any], formats: dict[int, tuple[int, int]]
) -> dict[int, tuple[int, int]]:
    """Drop any format id whose SysInfoExtended entry has a nonzero
    `AssociatedFormat` — see the big comment above for how this was
    confirmed against a real iTunes-authored ArtworkDB byte-diff.
    `extract_image_formats()` doesn't preserve `AssociatedFormat` (it
    only reads FormatId/width/height), so this re-scans the same raw
    `AlbumArt`-family plist entries directly to recover it. An entry
    missing the field entirely is kept (treated as unassociated/plain),
    so devices that don't declare it at all behave exactly as before."""
    from iopenpod.device.sysinfo import COVER_ART_KEYS

    associated: dict[int, int] = {}
    for key in COVER_ART_KEYS:
        value = plist.get(key)
        if not isinstance(value, list):
            continue
        for entry in value:
            if not isinstance(entry, dict):
                continue
            fmt_id = entry.get("FormatId") or entry.get("CorrelationID") or entry.get("format_id")
            if fmt_id is None:
                continue
            try:
                fmt_int = int(fmt_id)
            except (TypeError, ValueError):
                continue
            try:
                associated[fmt_int] = int(entry.get("AssociatedFormat", 0) or 0)
            except (TypeError, ValueError):
                associated[fmt_int] = 0

    filtered = {fid: dims for fid, dims in formats.items() if associated.get(fid, 0) == 0}
    dropped = sorted(fid for fid in formats if fid not in filtered)
    if dropped:
        logger.info(
            "Excluding non-plain AlbumArt formats %s (nonzero AssociatedFormat — "
            "not part of the normal per-track thumbnail set)",
            dropped,
        )
    return filtered


def _read_device_album_art_formats(mount_path: str) -> dict[int, tuple[int, int]]:
    """Parse the real, on-device `AlbumArt` format list from SysInfoExtended.

    Returns {} (never raises) on any read/parse failure, so callers can
    safely fall back to iopenpod's static per-family table."""
    import plistlib

    from iopenpod.device.sysinfo import COVER_ART_KEYS, extract_image_formats

    sysinfo_extended_path = Path(mount_path) / "iPod_Control" / "Device" / "SysInfoExtended"
    try:
        raw = sysinfo_extended_path.read_bytes()
    except OSError:
        return {}

    try:
        plist = plistlib.loads(_sanitize_sysinfo_extended_plist(raw))
    except Exception:
        logger.debug("Could not parse %s as plist even after sanitizing", sysinfo_extended_path)
        return {}

    formats = extract_image_formats(plist, COVER_ART_KEYS)
    formats = _filter_to_plain_album_art_formats(plist, formats)
    if formats:
        logger.info(
            "Using device-reported AlbumArt formats from SysInfoExtended: %s",
            sorted(formats),
        )
    return formats


class _ThrottledProgressPrinter:
    """Wraps a plain string sink so high-frequency progress callbacks
    don't flood the terminal with one line per file — confirmed live in
    iopenpod's pc_library.py scan loop that its progress_callback fires
    completely unthrottled, once per file (thousands of calls for a real
    library/device). Always emits on a stage change or completion,
    otherwise at most once per min_interval seconds. See notes.md."""

    def __init__(self, sink: Callable[[str], None], min_interval: float = 1.0):
        self._sink = sink
        self._min_interval = min_interval
        self._last_stage: str | None = None
        self._last_time = 0.0

    def emit(self, stage: str, current: int, total: int, detail: str) -> None:
        now = time.monotonic()
        stage_changed = stage != self._last_stage
        done = bool(total) and current >= total
        if not (stage_changed or done or now - self._last_time >= self._min_interval):
            return
        self._last_stage = stage
        self._last_time = now
        progress = f" {current}/{total}" if total else ""
        suffix = f" — {detail}" if detail else ""
        self._sink(f"[{stage}]{progress}{suffix}")


def _backup_progress_adapter(
    sink: Callable[[str], None],
) -> Callable[[BackupProgress], None]:
    printer = _ThrottledProgressPrinter(sink)

    def _on_progress(p: BackupProgress) -> None:
        printer.emit(p.stage, p.current, p.total, p.current_file or p.message)

    return _on_progress


def _engine_progress_adapter(
    sink: Callable[[str], None],
) -> Callable[[EngineProgress], None]:
    printer = _ThrottledProgressPrinter(sink)

    def _on_progress(p: EngineProgress) -> None:
        printer.emit(str(p.stage), p.current, p.total, p.message)

    return _on_progress


@dataclass(frozen=True)
class _DeviceStorage:
    """Minimal stand-in for application.services.DeviceStorageSnapshot,
    built straight from DeviceInfo fields so this never has to import the
    application package — see docs/m6-ipod-headless-recommendation.md's
    "application's __init__.py is not itself Qt-free" section."""

    reported_volume_format: str
    scanned_filesystem_type: str
    device_max_file_size_bytes: int | None
    volume_identity_key: str = ""

    @classmethod
    def from_device_info(cls, info: DeviceInfo) -> "_DeviceStorage":
        max_gb = float(getattr(info, "max_file_size_gb", 0) or 0)
        return cls(
            reported_volume_format=str(info.reported_volume_format or ""),
            scanned_filesystem_type=str(info.filesystem_type or ""),
            device_max_file_size_bytes=int(max_gb * 1024**3) if max_gb > 0 else None,
            volume_identity_key=str(info.volume_identity_key or ""),
        )


def _parse_published_at(value: str) -> float:
    """published_at can be an RSS pubDate (RFC 822, e.g. "Sun, 01 Mar 2026
    00:00:00 -0000") or a Pocket Casts ISO timestamp (e.g.
    "2026-03-01T00:00:00Z") — download.py falls back between the two
    depending on which source actually provided this episode's metadata.
    Returns 0.0 (never raises) on anything else, including a blank
    string for episodes fetched before this field existed."""
    if not value:
        return 0.0
    try:
        return email.utils.parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _load_podcast_feeds(
    db_path: str, library_root: Path, profile: ProfileConfig
) -> list[PodcastFeed]:
    """Builds PodcastFeed/PodcastEpisode objects directly from
    podcast-manager's own state DB — no file-tag dependency needed (see
    docs/m6-ipod-headless-recommendation.md's podcast section).

    Confirmed live (2026-08-18): episodes marked played in our own state
    db (via Pocket Casts sync or a manual `update_play_state` call) kept
    getting added to the device anyway. Root cause: this function never
    set `PodcastEpisode.listened_override`, so iopenpod's own
    `_episode_was_listened()` (podcasts/podcast_sync.py) had no way to
    know about our played state at all — it falls back to `play_count`,
    which only ever gets populated from *device-observed* play history
    (`_update_episode_playback_from_track`, driven off an iPod track's own
    play count read back on a previous sync), never from our state db.
    So an episode only stopped being re-added once the device itself had
    already recorded a play of it — our own `played` flag was silently
    ignored by the add/remove decision entirely. Setting
    `listened_override=True` (not `False`) for played episodes and
    leaving it `None` otherwise is required — `None` means "trust device
    history", `False` is a *sticky* override that would block
    `_update_episode_playback_from_track` from ever recording real
    on-device play data for an episode we haven't marked played.

    Also confirmed live the same day: PodcastFeed was always built with
    no episode_slots/fill_mode, silently running on iopenpod's dataclass
    default (episode_slots=3) instead of the profile's real
    max_episodes_per_show -- same "config field exists but nothing reads
    it" shape as transcode_format/push_play_status_back. And pub_date was
    never set at all (stayed 0.0 for every episode), undermining
    fill_mode="newest"'s own sort reliability -- now populated from the
    published_at column.

    description/episode_number/season_number are threaded through too
    (added to EpisodeRecord for RSS-sourced metadata, see notes.md) --
    iopenpod's own _track_conversion.py genuinely writes these into the
    real on-device track (season_number/episode_number as mhit fields,
    description as the "Description Text" mhod shown in the device's own
    Podcasts UI), not just used for sync-planning like pub_date is."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT episode_uuid, podcast_uuid, show_name, local_path, "
        "title, audio_url, duration_seconds, played, published_at, "
        "description, episode_number, season_number FROM episodes"
    ).fetchall()
    conn.close()

    feeds_by_show: dict[str, PodcastFeed] = {}
    for row in rows:
        feed = feeds_by_show.get(row["podcast_uuid"])
        if feed is None:
            feed = PodcastFeed(
                feed_url=f"podcast-manager:{row['podcast_uuid']}",
                title=row["show_name"],
                episode_slots=profile.podcasts.max_episodes_per_show,
                fill_mode=profile.podcasts.fill_modes.get(row["podcast_uuid"], "newest"),
                clear_when_listened=True,
            )
            feeds_by_show[row["podcast_uuid"]] = feed

        local_path = Path(row["local_path"])
        if not local_path.is_absolute():
            local_path = library_root / local_path
        feed.episodes.append(
            PodcastEpisode(
                guid=row["episode_uuid"],
                title=row["title"] or Path(row["local_path"]).stem,
                description=row["description"],
                audio_url=row["audio_url"],
                duration_seconds=row["duration_seconds"],
                episode_number=row["episode_number"],
                season_number=row["season_number"],
                downloaded_path=str(local_path) if local_path.is_file() else "",
                listened_override=True if row["played"] else None,
                pub_date=_parse_published_at(row["published_at"]),
            )
        )

    return list(feeds_by_show.values())


# Every mhii (ArtworkDB image-index) entry real iTunes writes has a third
# child chunk beyond the two per-format THUMBNAIL_IMAGE containers: an mhod
# of type 6 (iopenpod's own artworkdb_shared/constants.py already names this
# ArtworkMhodType.UNKNOWN_CONTAINER_6 = 6, so it's a recognized-but-unwritten
# format element, not a guess), wrapping a fixed 96-byte all-zero "mhaf"
# sub-chunk. iopenpod's own _write_mhii() never emits it.
#
# Confirmed live and exhaustively, not just spot-checked: byte-diffed a
# freshly iTunes-written ArtworkDB (1141 tracks) against the ArtworkDB this
# project's own sync-orchestrator had written for the same device
# (recovered from its own backup snapshot, since the iTunes resync had
# since overwritten the device) — every single one of 1141/1141 real-iTunes
# entries has this chunk; 0/5555 of iopenpod's entries do. Identical bytes
# across every entry checked, confirming it's static boilerplate, not
# per-track computed data. See notes.md's "iopenpod ... ArtworkDB" section.
#
# This was the actual remaining root cause behind album art never
# displaying on-device despite every other layer (capabilities/format
# resolution, pixel data, iTunesDB<->ArtworkDB link) already being
# confirmed correct: the *data* was always right, but the on-disk *shape*
# of every entry didn't match what real firmware expects, which is
# consistent with firmware silently declining to render a
# structurally-incomplete-looking entry instead of erroring.
_MHII_MISSING_INDEX_CHUNK = bytes.fromhex(
    "6d686f6418000000780000000600000000000000000000006d686166600000003c000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000"
)

# Captured once at import time so repeated calls to
# _apply_missing_artwork_index_chunk_workaround() stay idempotent — each
# wrapped call always delegates to this untouched original, rather than
# wrapping whatever iopenpod.artworkdb_writer.artworkdb_chunks._write_mhii
# happens to currently point at (which would double-append the chunk on a
# second call in the same process, e.g. plan_sync running more than once).
_write_mhii_original = _artworkdb_chunks._write_mhii


def _write_mhii_with_missing_index_chunk(entry: Any, format_locations: Any) -> bytes:
    data = bytearray(_write_mhii_original(entry, format_locations))
    total_len, child_count = struct.unpack_from("<II", data, 8)
    struct.pack_into("<I", data, 8, total_len + len(_MHII_MISSING_INDEX_CHUNK))
    struct.pack_into("<I", data, 12, child_count + 1)
    return bytes(data) + _MHII_MISSING_INDEX_CHUNK


def _apply_missing_artwork_index_chunk_workaround() -> None:
    """Patch iopenpod's ArtworkDB mhii writer to match real iTunes' entry
    shape — see _MHII_MISSING_INDEX_CHUNK above for the full writeup."""
    _artworkdb_chunks._write_mhii = _write_mhii_with_missing_index_chunk


# iopenpod's _write_mhfd() (the ArtworkDB root/mhfd header) hardcodes byte
# offset 16 ("unk2" in artworkdb_parser/mhfd_parser.py, commented there as
# "1 until iTunes 4.9, 2 after") to a fixed 2, unconditionally. Confirmed
# live (2026-08-18): byte-diffing a real iTunes-authored ArtworkDB (pulled
# straight off this device) against ours — after the AssociatedFormat fix
# above already made every mhli entry byte-for-byte structurally identical
# (same field layout, same dimensions, same mhod count/order) — turned up
# exactly one remaining top-level difference: this field is 6 in the real
# file, 2 in ours. The stale "1 until iTunes 4.9, 2 after" comment clearly
# doesn't hold for whatever iTunes version wrote this device's file, so 2
# is very likely just wrong for this device family rather than a
# deliberately-chosen default.
#
# iopenpod already preserves two OTHER header ranges (bytes 32:48 and
# 60:68) from a caller-supplied reference_mhfd (the device's own existing
# ArtworkDB, read before it gets overwritten) — presumably for the same
# "firmware validates header continuity" reason. This field would be a
# natural third range to preserve the same way, *except* the device's
# ArtworkDB has already been overwritten once this session with our own
# (wrong) value of 2 — so from now on, "preserve whatever's already on the
# device" would just perpetuate our own bug forever instead of fixing it.
# Hardcoding the one real value observed, the same way the mhod6 chunk
# workaround above hardcodes its own empirically-confirmed constant, is
# the only option that actually self-corrects on the very next sync.
_write_mhfd_original = _artworkdb_chunks._write_mhfd
_MHFD_UNK2_REAL_ITUNES_VALUE = 6


def _write_mhfd_with_real_unk2(
    datasets: list[bytes], next_mhii_id: int, reference_mhfd: bytes | None = None
) -> bytes:
    data = bytearray(_write_mhfd_original(datasets, next_mhii_id, reference_mhfd))
    struct.pack_into("<I", data, 16, _MHFD_UNK2_REAL_ITUNES_VALUE)
    return bytes(data)


def _apply_mhfd_unk2_workaround() -> None:
    """Patch iopenpod's ArtworkDB mhfd writer to match real iTunes' header
    byte 16 instead of iopenpod's own hardcoded (and, per live evidence,
    wrong) 2 — see _write_mhfd_with_real_unk2 above for the full writeup."""
    _artworkdb_chunks._write_mhfd = _write_mhfd_with_real_unk2


# iopenpod's write_mhit() never populates a widened "duplicate Store
# metadata" block real iTunes always writes for these tracks: bytes 0x194
# (movie_flag_2), 0x195 (purchased_aac_flag_2), and the four u64 fields at
# 0x1B0/0x1B8/0x1C0/0x1D0/0x1D8 (store_track_id_2/store_encoder_version_2/
# store_artist_id_2/store_album_id_2/store_content_flag_2) — all left at
# their FieldDef default of 0. Confirmed live (2026-08-19): byte-diffed a
# real iTunes-written iTunesDB against ours for every track shared between
# them (8/8) — real always has these populated as an exact low-word mirror
# of the corresponding primary field already at 0xB1/0x93/0xE0/0xE4/0xE8/
# 0xF0/0xF4 (itunesdb_shared/mhit_defs.py's own research notes confirm
# this: "Low words exactly mirror +0xE0..+0xF4"), ours always has them
# zeroed. child_count is 14 in every real entry, 13 in every one of ours —
# this missing block is exactly the accounting for that gap.
#
# Low-confidence fix, tried anyway per explicit request: mhit_defs.py's own
# research notes mark nearly every one of these fields "not visibly
# promoted by the 2.0.1 MHIT loader" — i.e. iopenpod's own prior firmware
# reverse-engineering concluded the device doesn't actually read them, so
# this may turn out to be real-but-inert data rather than the artwork
# rendering fix. Deliberately narrow: only the fields with a documented,
# unambiguous "exact mirror of an already-correct primary field" relationship
# are populated — fields with no confirmed derivation (unk0xC4, unk0x154,
# unk0x164, unk0x168, unk0x197, unk0x20C) are left untouched rather than
# guessed, consistent with this project's "abort rather than guess" stance
# elsewhere (e.g. supports_artwork=False handling).
#
# write_mhit is called via `from .mhit_writer import write_mhit` inside
# itunesdb_writer/mhlt_writer.py — a direct name import, so patching
# mhit_writer.write_mhit alone would NOT affect mhlt_writer's own
# already-bound reference (the same "caller's own bound name doesn't
# see a later patch to the origin module" gotcha this project already
# documented for services/common's non-editable venv install — see
# notes.md). Patching _mhlt_writer.write_mhit directly, the actual call
# site, is what's required for this to take effect.
_write_mhit_original = _mhlt_writer.write_mhit


def _write_mhit_with_duplicate_store_fields(
    track: Any, track_id: int, db_id_2: int = 0, capabilities: Any = None, db_version: int = 0
) -> bytes:
    data = bytearray(
        _write_mhit_original(
            track, track_id, db_id_2, capabilities=capabilities, db_version=db_version
        )
    )
    if len(data) >= 0x1E0:
        movie_flag = data[0xB1]
        purchased_aac_flag = data[0x93]
        store_track_id = struct.unpack_from("<I", data, 0xE0)[0]
        store_encoder_version = struct.unpack_from("<I", data, 0xE4)[0]
        store_artist_id = struct.unpack_from("<I", data, 0xE8)[0]
        store_album_id = struct.unpack_from("<I", data, 0xF0)[0]
        store_content_flag = struct.unpack_from("<I", data, 0xF4)[0]

        data[0x194] = movie_flag
        data[0x195] = purchased_aac_flag
        struct.pack_into("<Q", data, 0x1B0, store_track_id)
        struct.pack_into("<Q", data, 0x1B8, store_encoder_version)
        struct.pack_into("<Q", data, 0x1C0, store_artist_id)
        struct.pack_into("<Q", data, 0x1D0, store_album_id)
        struct.pack_into("<Q", data, 0x1D8, store_content_flag)
    return bytes(data)


def _apply_mhit_duplicate_store_fields_workaround() -> None:
    """Patch iopenpod's MHIT writer to mirror movie_flag/purchased_aac_flag/
    store_* into their real-iTunes-observed "_2" duplicate slots — see
    _write_mhit_with_duplicate_store_fields above for the full writeup."""
    _mhlt_writer.write_mhit = _write_mhit_with_duplicate_store_fields


def _register_current_device(info: DeviceInfo) -> Any:
    """EngineRequest.device_capabilities doesn't reach the actual
    write-time decision: iopenpod.sync._db_io and the real ArtworkDB
    writer (artworkdb_writer/rgb565.py) both re-resolve capabilities/
    formats themselves via a private in-process device registry
    (get_current_device_for_path). Our headless path never calls
    iopenpod's own set_current_device(), so patch that registry to
    return our own DeviceInfo instance ourselves — every consumer needs
    this, regardless of how each individual iopenpod module happened to
    import capabilities_for_family_gen.

    Until iopenpod==1.67.0 this function also had to hand-correct
    model_family/generation: enrich() (device/info.py) used to resolve
    this real device's identity to a coarse, ambiguous placeholder
    (model_family="iPod", generation="") instead of the more specific
    "iPod Video"/"5.5th Gen" its own SysInfo reports, because USB PID
    0x1209 is shared by 5th/5.5th gen and Linux had no privilege-safe
    way to read the real Apple product serial needed to disambiguate
    them. Confirmed fixed (2026-07-30): once iopenpod's own
    61-iopenpod.rules udev rule is installed (see notes.md), enrich()
    resolves this device natively — model_family='iPod',
    generation='5.5th Gen', model_number='MA450', full artwork
    capabilities including cover_art_formats 1028/1029 — with no
    override needed. Direct enrich() call against the real device
    confirmed this; no more "cached family conflicts with live USB
    PID... using live USB identity" collapse. See notes.md for the full
    verification."""
    _iopenpod_device.get_current_device_for_path = lambda path: info

    # See _read_device_album_art_formats' docstring: enrich() populates
    # info.artwork_formats from a static per-family table and never
    # consults the device's own (real, authoritative) SysInfoExtended
    # AlbumArt list, so override it here when we can read one. iopenpod's
    # own resolve_cover_art_format_definitions_for_device() already
    # treats device.artwork_formats as authoritative "observed" data when
    # present (device/artwork.py) — falling back correctly per-format to
    # the static table, the global registry, or a generic RGB565
    # definition — so populating this one attribute is sufficient; no
    # other iopenpod code needs patching.
    device_album_art_formats = _read_device_album_art_formats(info.path)
    if device_album_art_formats:
        info.artwork_formats = device_album_art_formats

    capabilities = info.capabilities
    if not capabilities.cover_art_formats:
        # Defensive fallback for any genuinely unrecognized device (not
        # this project's known 5.5th Gen unit): force
        # supports_artwork=False on the object we return rather than
        # risk a bad ArtworkDB write. Deliberately scoped to the return
        # value only, not a global capabilities_for_family_gen patch —
        # write_itunesdb already has a documented "abort rather than
        # guess" default for supports_artwork=False.
        capabilities = dataclasses.replace(capabilities, supports_artwork=False)
    return capabilities


@dataclass
class PlannedSync:
    plan: Any
    device_info: DeviceInfo
    itunesdb_path: str
    before_track_count: int
    capabilities: Any
    storage: Any
    options: EngineOptions
    snapshot: SnapshotInfo | None
    unresolved_selections: list[str] = dataclasses.field(default_factory=list)
    unresolved_audiobook_selections: list[str] = dataclasses.field(default_factory=list)
    unresolved_music_selections: list[str] = dataclasses.field(default_factory=list)
    # Count of local episodes whose play state changed vs. what was
    # already recorded, per resolve_played_states — see playstate.py.
    play_states_updated: int = 0


# common.models.SyncSettings.transcode_format is an unconstrained `str` in
# the schema (profiles predate this mapping), so validate against a known
# set here rather than silently guessing at an unrecognized value — same
# "fail loud" preference as the rest of this module's config handling.
_TRANSCODE_FORMAT_TO_PREFER_LOSSY = {"alac": False, "aac": True}


def _transcode_options_for(profile: ProfileConfig) -> TranscodeOptions:
    """Maps `profile.sync.transcode_format` to iopenpod's real transcoding
    knob (`TranscodeOptions.prefer_lossy`), which chooses AAC over ALAC for
    lossless sources during PC->iPod sync.

    Found live (2026-08-15, setting up a second device with a tighter
    capacity budget): this mapping never existed — `plan_sync`'s
    `EngineOptions(...)` never set `transcode_options` at all, so every
    sync silently used iopenpod's own default (`TranscodeOptions()`,
    `prefer_lossy=False`) regardless of what a profile's `transcode_format`
    said. `transcode_format: aac` in a profile was a complete no-op. See
    notes.md for the full investigation."""
    try:
        prefer_lossy = _TRANSCODE_FORMAT_TO_PREFER_LOSSY[profile.sync.transcode_format]
    except KeyError:
        raise SyncError(
            f"profile {profile.profile!r} has sync.transcode_format="
            f"{profile.sync.transcode_format!r}, but only "
            f"{sorted(_TRANSCODE_FORMAT_TO_PREFER_LOSSY)} are supported"
        ) from None
    return TranscodeOptions(prefer_lossy=prefer_lossy)


def plan_sync(
    *,
    device_info: DeviceInfo,
    library_root: str | Path,
    state_root: str | Path,
    profile: ProfileConfig,
    extra_pc_folders: tuple[str, ...] = (),
    skip_backup: bool = False,
    skip_podcasts: bool = False,
    backup_dir: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> PlannedSync:
    """Computes (but does not write) a full sync plan: music + playlists
    (via SyncEngine.PLAN against pc_folders) merged with podcasts (via
    build_podcast_sync_plan) — the same merge approach the real iopenpod
    app uses (application/sync_session.py): extend to_add, sum storage."""
    ipod_path = device_info.path
    itunesdb_path = resolve_itdb_path(ipod_path)
    if not itunesdb_path:
        raise SyncError(f"could not resolve iTunesDB path under {ipod_path}")

    library_root = Path(library_root)
    state_root = Path(state_root)

    unresolved_selections: list[str] = []
    external_library_folders: tuple[str, ...] = ()
    if profile.external_library is not None:
        ext = profile.external_library
        if not Path(ext.path).is_dir():
            raise SyncError(f"external_library path not found: {ext.path}")
        selected_files, unresolved_selections = resolve_selected_files(
            ext.path, ext.selections, mode=ext.mode
        )
        staging_dir = state_root / ".external_library_staging" / profile.profile
        build_staging_dir(staging_dir, ext.path, selected_files)
        external_library_folders = (str(staging_dir),)

    audiobooks_folders, unresolved_audiobook_selections = resolve_audiobooks_folder(
        library_root / "audiobooks",
        profile.audiobooks,
        state_root / ".audiobooks_staging" / profile.profile,
    )

    # Only narrows the device's *general* library beyond this profile's
    # own playlists — a playlist's own tracks are always included
    # regardless (see resolve_music_folder's docstring for why removing
    # library/music from pc_folders never drops a playlist's tracks).
    music_folders, unresolved_music_selections = resolve_music_folder(
        library_root / "music",
        profile.music,
        state_root / ".music_staging" / profile.profile,
    )

    pc_folders = (
        *music_folders,
        str(library_root / "playlists" / profile.profile),
        *external_library_folders,
        *audiobooks_folders,
        *extra_pc_folders,
    )
    media_folders = build_media_folders(pc_folders)
    for folder in pc_folders:
        if not Path(folder).is_dir():
            raise SyncError(f"pc folder not found: {folder}")

    backup_mgr = BackupManager(
        device_id=device_info.serial or device_info.firewire_guid or profile.profile,
        backup_dir=backup_dir or str(state_root / "device_backups"),
        device_name=device_info.ipod_name or profile.device.match_value,
    )
    snapshot: SnapshotInfo | None = None
    if not skip_backup:
        snapshot = backup_mgr.create_backup(
            ipod_path,
            progress_callback=(
                _backup_progress_adapter(progress_callback) if progress_callback else None
            ),
            reported_volume_format=device_info.reported_volume_format,
            expected_volume_identity_key=device_info.volume_identity_key,
        )
        if snapshot is None:
            raise SyncError("backup did not produce a snapshot; refusing to write")

    before = load_ipod_library(itunesdb_path)
    if before is None:
        raise SyncError("could not parse iTunesDB")
    before_tracks = before.get("mhlt", [])
    before_playlists = before.get("mhlp", [])

    play_states_updated = 0
    state_db_path = state_root / f"{profile.profile}.sqlite"
    # Populated below whenever podcasts are in play — carried forward to the
    # removal-plan step near the end of this function (after `plan` exists)
    # so that step sees this run's own device read-back, not stale played
    # flags from before it.
    known_episodes: list[Any] = []
    if not skip_podcasts and state_db_path.is_file():
        # Read-only (MappingManager.load() never touches on-device files)
        # and independent of --execute: real listening progress since the
        # last sync is already sitting in the device's Play Counts file
        # the moment before_tracks is parsed. See playstate.py/notes.md.
        mapping = MappingManager(ipod_path).load()
        with StateDB(state_db_path) as db:
            episodes_by_path = {e.local_path: e for e in db.list_episodes()}
            durations_by_path = {
                path: e.duration_seconds for path, e in episodes_by_path.items()
            }
            played_states = resolve_played_states(before, mapping, durations_by_path)
            for local_path, (played, played_up_to) in played_states.items():
                episode = episodes_by_path.get(local_path)
                if episode is None:
                    continue
                if episode.played == played and episode.played_up_to == played_up_to:
                    continue
                if db.update_play_state(
                    episode.episode_uuid, played=played, played_up_to=played_up_to
                ):
                    play_states_updated += 1
            # Re-read rather than reuse episodes_by_path.values(): the
            # update_play_state calls above may have just changed some of
            # these rows in this very call.
            known_episodes = db.list_episodes()

    fpcalc_path = shutil.which("fpcalc") or ""
    if not fpcalc_path:
        raise SyncError("fpcalc not found on PATH (chromaprint not installed)")

    capabilities = _register_current_device(device_info)
    _apply_missing_artwork_index_chunk_workaround()
    _apply_mhfd_unk2_workaround()
    _apply_mhit_duplicate_store_fields_workaround()
    storage = _DeviceStorage.from_device_info(device_info)
    options = EngineOptions(
        supports_video=capabilities.supports_video,
        supports_podcast=capabilities.supports_podcast,
        supports_photo=capabilities.supports_photo,
        fpcalc_path=fpcalc_path,
        transcode_options=_transcode_options_for(profile),
    )

    plan_outcome = SyncEngine().run(
        EngineRequest(
            operation=EngineOperation.PLAN,
            ipod_path=ipod_path,
            pc_folders=media_folders,
            ipod_tracks=tuple(before_tracks),
            existing_playlists=tuple(before_playlists),
            options=options,
            device_info=device_info,
            device_capabilities=capabilities,
            device_storage=storage,
            progress_callback=(
                _engine_progress_adapter(progress_callback) if progress_callback else None
            ),
        )
    )

    # iopenpod only ever saves the fingerprint cache after PC-side
    # scanning, never after device-side fingerprinting — force a save so
    # this run's device-side work isn't silently discarded. See
    # docs/m6-ipod-headless-recommendation.md.
    FingerprintCache.get_instance().save()

    if not plan_outcome.success:
        messages = "; ".join(
            f"[{d.stage}] {d.code}: {d.message}" for d in plan_outcome.diagnostics
        )
        raise SyncError(f"planning failed: {messages}")

    plan = plan_outcome.result

    if not skip_podcasts:
        if state_db_path.is_file():
            feeds = _load_podcast_feeds(str(state_db_path), library_root, profile)
            for feed in feeds:
                episode_feed_pairs = [
                    (ep, feed) for ep in feed.episodes if ep.downloaded_path
                ]
                if not episode_feed_pairs:
                    continue
                podcast_plan = build_podcast_sync_plan(episode_feed_pairs, before_tracks)
                if not podcast_plan.to_add:
                    continue
                plan.to_add.extend(podcast_plan.to_add)
                plan.storage.bytes_to_add += podcast_plan.storage.bytes_to_add

            # build_podcast_sync_plan above only ever proposes ADD_TO_IPOD
            # for episodes not yet on the device -- an episode already
            # synced (the overwhelming majority, on a real device) is
            # filtered out and never revisited for artwork. This is the
            # separate, targeted backfill for those. See
            # podcast_artwork_backfill.py and notes.md.
            artwork_items, artwork_pc_paths = build_podcast_artwork_backfill_items(
                feeds, before_tracks
            )

            # Deliberately keyed off known_episodes (the state db's played
            # flag), not _load_podcast_feeds' downloaded_path filter above —
            # podcast-manager typically deletes a played episode's file
            # before this ever runs, so a file-presence check would miss
            # exactly the episodes this is supposed to remove. Flows through
            # the same --allow-removals gate as any other to_remove, in
            # cli.py.
            removal_items = build_podcast_removal_items(known_episodes, before_tracks)

            # See exclude_conflicting_with_removal's docstring: an episode
            # discovered as newly-played by this exact run can otherwise
            # end up proposed for both an artwork update and a removal at
            # once, which iopenpod's plan validator correctly refuses to
            # execute. Removal always wins.
            artwork_items, artwork_pc_paths = exclude_conflicting_with_removal(
                artwork_items, artwork_pc_paths, removal_items
            )

            if artwork_items:
                plan.to_update_artwork.extend(artwork_items)
                plan.matched_pc_paths.update(artwork_pc_paths)

            if removal_items:
                plan.to_remove.extend(removal_items)
                plan.storage.bytes_to_remove += sum(
                    item.ipod_track.get("size", 0)
                    for item in removal_items
                    if item.ipod_track
                )

    return PlannedSync(
        plan=plan,
        device_info=device_info,
        itunesdb_path=itunesdb_path,
        before_track_count=len(before_tracks),
        capabilities=capabilities,
        storage=storage,
        options=options,
        snapshot=snapshot,
        unresolved_selections=unresolved_selections,
        unresolved_music_selections=unresolved_music_selections,
        unresolved_audiobook_selections=unresolved_audiobook_selections,
        play_states_updated=play_states_updated,
    )


def execute_sync(
    planned: PlannedSync, progress_callback: Callable[[str], None] | None = None
) -> tuple[Any, dict]:
    """Executes a previously computed plan and re-reads the device
    afterward to verify. Callers must have already decided the plan is
    safe to execute (see cli.py's hard gate on unexpected removals) —
    this function does not re-check plan.to_remove itself, to keep that
    safety decision visible at the call site rather than buried here."""
    exec_outcome = SyncEngine().run(
        EngineRequest(
            operation=EngineOperation.EXECUTE,
            ipod_path=planned.device_info.path,
            plan=planned.plan,
            options=planned.options,
            device_info=planned.device_info,
            device_capabilities=planned.capabilities,
            device_storage=planned.storage,
            progress_callback=(
                _engine_progress_adapter(progress_callback) if progress_callback else None
            ),
        )
    )
    exec_result = exec_outcome.result
    if not exec_outcome.success or (exec_result is not None and exec_result.has_errors):
        messages = "; ".join(
            f"[{d.stage}] {d.code}: {d.message}" for d in exec_outcome.diagnostics
        )
        if exec_result is not None:
            messages += "; " + "; ".join(f"[{s}] {m}" for s, m in exec_result.errors)
        raise SyncError(f"execution failed: {messages}")

    after = load_ipod_library(planned.itunesdb_path)
    if after is None:
        raise SyncError("could not re-parse iTunesDB after write")

    return exec_result, after
