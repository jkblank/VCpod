"""Plain filesystem-mirror sync for Rockbox-loaded devices.

No iTunesDB, no ArtworkDB, no iopenpod SyncEngine anywhere in this module —
Rockbox ignores both Apple databases and reads metadata/art straight from
each file's own tags. This is a deliberately separate code path from
sync.py rather than iopenpod's own `rockbox_metadata_support` bolt-on: that
option still writes a full iTunesDB (still exposed to the paused
ArtworkDB size-ceiling investigation, see notes.md) and never places real
playlist files on the device (iopenpod's playlist-file handling parses
.m3u8 files into native iTunesDB playlist objects at scan time and never
copies the physical file) — Rockbox would see no playlists under that
approach. See the "Rockbox support" plan for the full comparison.

Reuses everything from the existing pipeline that isn't iTunesDB-specific:
selection.py's folder-resolution helpers, iopenpod's BackupManager (a
generic content-addressed file-tree backup) and transcoder (standalone
functions, no SyncEngine dependency).
"""

from __future__ import annotations

import logging
import os
import posixpath
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from common.models import ProfileConfig
from common.playlist import _read_existing_entries as _read_m3u8_entries
from iopenpod.device.info import DeviceInfo
from iopenpod.sync.backup_manager import BackupManager, SnapshotInfo
from iopenpod.sync.transcoder import (
    TranscodeOptions,
    TranscodePlan,
    TranscodeTarget,
    resolve_transcode_plan,
    transcode,
)

from sync_orchestrator.selection import (
    resolve_audiobooks_folder,
    resolve_music_folder,
    resolve_selected_files,
)
from sync_orchestrator.sync import _backup_progress_adapter, _parse_published_at

logger = logging.getLogger(__name__)

# Top-level device folders this module owns. Deliberately namespaced
# per-source (rather than one flat "Music" mixing music/audiobooks/
# podcasts/external_library) so two source roots can never collide on the
# same relative path.
MUSIC_DIRNAME = "Music"
PLAYLISTS_DIRNAME = "Playlists"
AUDIOBOOKS_DIRNAME = "Audiobooks"
PODCASTS_DIRNAME = "Podcasts"
EXTERNAL_LIBRARY_DIRNAME = "ExternalLibrary"
_MANAGED_DIRNAMES = (
    MUSIC_DIRNAME,
    AUDIOBOOKS_DIRNAME,
    PODCASTS_DIRNAME,
    EXTERNAL_LIBRARY_DIRNAME,
    PLAYLISTS_DIRNAME,
)

_AUDIO_EXTENSIONS = frozenset(
    {".mp3", ".m4a", ".m4b", ".aac", ".flac", ".wav", ".aiff", ".aif", ".ogg", ".opus", ".wma"}
)

# FAT32 (the filesystem every real click-wheel iPod uses) only stores
# mtime to 2-second resolution — comparing with equality would false-
# positive "changed" on every single file, every sync.
_MTIME_TOLERANCE_SECONDS = 2.0

# Same mapping sync.py's _transcode_options_for uses for iTunes mode —
# duplicated rather than imported since sync.py's version raises SyncError
# (iTunesDB-flavored exception type), not RockboxSyncError.
_TRANSCODE_FORMAT_TO_PREFER_LOSSY = {"alac": False, "aac": True}


class RockboxSyncError(Exception):
    pass


def _transcode_options_for(profile: ProfileConfig) -> TranscodeOptions:
    try:
        prefer_lossy = _TRANSCODE_FORMAT_TO_PREFER_LOSSY[profile.sync.transcode_format]
    except KeyError:
        raise RockboxSyncError(
            f"profile {profile.profile!r} has sync.transcode_format="
            f"{profile.sync.transcode_format!r}, but only "
            f"{sorted(_TRANSCODE_FORMAT_TO_PREFER_LOSSY)} are supported"
        ) from None
    return TranscodeOptions(prefer_lossy=prefer_lossy)


@dataclass(frozen=True)
class RockboxSyncItem:
    """One audio file that needs to land on the device."""

    device_relative_path: str  # e.g. "Music/Artist/Album/01 Track.m4a"
    source_path: str
    transcode_plan: TranscodePlan


@dataclass
class RockboxSyncPlan:
    to_add: list[RockboxSyncItem] = field(default_factory=list)
    to_update: list[RockboxSyncItem] = field(default_factory=list)
    to_remove: list[str] = field(default_factory=list)  # device-relative paths
    playlists_to_add: dict[str, str] = field(default_factory=dict)  # device-relative -> content
    playlists_to_update: dict[str, str] = field(default_factory=dict)
    bytes_to_add: int = 0


@dataclass
class PlannedRockboxSync:
    plan: RockboxSyncPlan
    device_info: DeviceInfo
    snapshot: SnapshotInfo | None
    before_file_count: int
    unresolved_selections: list[str] = field(default_factory=list)
    unresolved_audiobook_selections: list[str] = field(default_factory=list)
    unresolved_music_selections: list[str] = field(default_factory=list)


def _iter_audio_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS)


def _add_desired_file(
    source_path: Path,
    source_root: Path,
    device_subdir: str,
    transcode_options: TranscodeOptions,
    desired: dict[str, RockboxSyncItem],
) -> str:
    """Adds source_path to `desired` (keyed by its resolved device-relative
    path) and returns that same key, so callers that need to reference the
    item they just added (e.g. the playlist track lookup below) don't have
    to recompute it."""
    relative = source_path.relative_to(source_root)
    tplan = resolve_transcode_plan(source_path, options=transcode_options)
    device_relative = (Path(device_subdir) / relative.with_suffix(tplan.output_extension)).as_posix()
    desired[device_relative] = RockboxSyncItem(
        device_relative_path=device_relative,
        source_path=str(source_path),
        transcode_plan=tplan,
    )
    return device_relative


def _collect_desired(
    source_root: Path,
    device_subdir: str,
    transcode_options: TranscodeOptions,
    desired: dict[str, RockboxSyncItem],
) -> None:
    for f in _iter_audio_files(source_root):
        _add_desired_file(f, source_root, device_subdir, transcode_options, desired)


def _walk_managed_device_files(device_root: Path) -> dict[str, float]:
    """relative posix path (e.g. "Music/Artist/Album/Track.m4a") -> mtime,
    for every file under this module's own managed top-level folders.
    Never touches iPod_Control, .rockbox, or anything else on the device —
    only the folders this module itself writes to."""
    existing: dict[str, float] = {}
    for dirname in _MANAGED_DIRNAMES:
        subroot = device_root / dirname
        if not subroot.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(subroot):
            for name in filenames:
                p = Path(dirpath) / name
                rel = (Path(dirname) / p.relative_to(subroot)).as_posix()
                existing[rel] = p.stat().st_mtime
    return existing


def _mtime_matches(a: float, b: float) -> bool:
    return abs(a - b) <= _MTIME_TOLERANCE_SECONDS


def _select_podcast_episode_files(state_db_path: Path, library_root: Path, profile: ProfileConfig) -> list[Path]:
    """Which downloaded episode files belong on the device, mirroring the
    profile's sync_unplayed_only/max_episodes_per_show/fill_modes — but
    computed directly against the state db rather than reusing
    iopenpod.podcasts.podcast_sync.build_podcast_sync_plan, which requires
    an iTunesDB-shaped "before" track list to do its own diffing. Simpler
    than iTunes mode's playcount-based removal (podcast_removal.py): an
    episode that falls out of this selection just becomes a normal
    to_remove in the general file diff, no separate removal-item type
    needed since there's no iTunesDB "track" object to build one from."""
    conn = sqlite3.connect(state_db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT podcast_uuid, local_path, played, published_at FROM episodes"
    ).fetchall()
    conn.close()

    by_show: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        if not row["local_path"]:
            continue
        if profile.podcasts.sync_unplayed_only and row["played"]:
            continue
        by_show.setdefault(row["podcast_uuid"], []).append(row)

    selected: list[Path] = []
    for podcast_uuid, episode_rows in by_show.items():
        fill_mode = profile.podcasts.fill_modes.get(podcast_uuid, "newest")
        episode_rows.sort(
            key=lambda r: _parse_published_at(r["published_at"]), reverse=(fill_mode == "newest")
        )
        for row in episode_rows[: profile.podcasts.max_episodes_per_show]:
            local_path = Path(row["local_path"])
            if not local_path.is_absolute():
                local_path = library_root / local_path
            if local_path.is_file():
                selected.append(local_path)
    return selected


def _render_m3u8_relative(playlist_device_relative: str, device_relative_tracks: list[str]) -> str:
    """Paths relative to the playlist file's own directory (the M3U spec's
    portable convention) — e.g. from "Playlists/Chill.m3u8",
    "Music/Artist/Track.m4a" becomes "../Music/Artist/Track.m4a"."""
    playlist_dir = posixpath.dirname(playlist_device_relative)
    lines = ["#EXTM3U", *(posixpath.relpath(t, start=playlist_dir) for t in device_relative_tracks)]
    return "\n".join(lines) + "\n"


def plan_rockbox_sync(
    *,
    device_info: DeviceInfo,
    library_root: str | Path,
    state_root: str | Path,
    profile: ProfileConfig,
    skip_backup: bool = False,
    skip_podcasts: bool = False,
    backup_dir: str | None = None,
    progress_callback: Callable[[str], None] | None = None,
) -> PlannedRockboxSync:
    library_root = Path(library_root)
    state_root = Path(state_root)
    device_root = Path(device_info.path)
    transcode_options = _transcode_options_for(profile)

    backup_mgr = BackupManager(
        device_id=device_info.serial or device_info.firewire_guid or profile.profile,
        backup_dir=backup_dir or str(state_root / "device_backups"),
        device_name=device_info.ipod_name or profile.device.match_value,
    )
    snapshot: SnapshotInfo | None = None
    if not skip_backup:
        snapshot = backup_mgr.create_backup(
            str(device_root),
            progress_callback=(
                _backup_progress_adapter(progress_callback) if progress_callback else None
            ),
            reported_volume_format=device_info.reported_volume_format,
            expected_volume_identity_key=device_info.volume_identity_key,
        )
        if snapshot is None:
            raise RockboxSyncError("backup did not produce a snapshot; refusing to write")

    desired: dict[str, RockboxSyncItem] = {}

    # Music: library/music, scoped per profile.music (defaults to the
    # whole shared pool, unfiltered — see MusicLibraryConfig). This only
    # narrows the device's *general* library beyond this profile's own
    # playlists — a playlist's own tracks are always included regardless
    # (see the playlist loop below), matching how a real iPod's on-device
    # Songs list and its playlists share one flat track table.
    music_root = library_root / "music"
    music_folders, unresolved_music_selections = resolve_music_folder(
        music_root, profile.music, state_root / ".music_staging" / profile.profile
    )
    for folder in music_folders:
        _collect_desired(Path(folder), MUSIC_DIRNAME, transcode_options, desired)

    unresolved_selections: list[str] = []
    if profile.external_library is not None:
        ext = profile.external_library
        if not Path(ext.path).is_dir():
            raise RockboxSyncError(f"external_library path not found: {ext.path}")
        selected_files, unresolved_selections = resolve_selected_files(
            ext.path, ext.selections, mode=ext.mode
        )
        for f in selected_files:
            _add_desired_file(f, Path(ext.path), EXTERNAL_LIBRARY_DIRNAME, transcode_options, desired)

    audiobooks_folders, unresolved_audiobook_selections = resolve_audiobooks_folder(
        library_root / "audiobooks",
        profile.audiobooks,
        state_root / ".audiobooks_staging" / profile.profile,
    )
    for folder in audiobooks_folders:
        # resolve_audiobooks_folder may hand back either the real
        # audiobooks_root directly (no filtering needed) or a staging dir
        # of per-selection symlinks — either way it's just a folder of
        # audio files from this module's point of view; relative-path
        # computation in _collect_desired works the same either way
        # (relative_to() only compares path strings, doesn't resolve
        # symlinks).
        _collect_desired(Path(folder), AUDIOBOOKS_DIRNAME, transcode_options, desired)

    if not skip_podcasts:
        state_db_path = state_root / f"{profile.profile}.sqlite"
        if state_db_path.is_file():
            podcasts_root = library_root / "podcasts"
            for episode_path in _select_podcast_episode_files(state_db_path, library_root, profile):
                try:
                    _add_desired_file(episode_path, podcasts_root, PODCASTS_DIRNAME, transcode_options, desired)
                except ValueError:
                    logger.warning(
                        "podcast episode %s is not under %s; skipping for Rockbox sync",
                        episode_path,
                        podcasts_root,
                    )

    # `existing` (what's already on the device) can be read now — it
    # doesn't depend on `desired`. The actual diff is computed further
    # below, after the playlist loop has had a chance to add any
    # playlist track that profile.music's scoping excluded from the
    # general pool above (a playlist's own tracks are never optional).
    existing = _walk_managed_device_files(device_root)

    # Playlists: read the .m3u8 files music-stack's fetch stage already
    # writes to library/playlists/{profile}/ (the same files iTunes mode
    # feeds into iopenpod's PCLibrary), remap each entry from a host-
    # absolute library/music path to this run's on-device relative path
    # (accounting for any extension change from transcoding), and write a
    # real, physical .m3u8 the device can browse directly — unlike iTunes
    # mode, where iopenpod only ever consumes these files as playlist
    # *definitions* and never copies the physical file to the device.
    # Reverse lookup (real source path -> already-resolved device path),
    # keyed by the *resolved* path rather than the raw source_path string
    # — when profile.music scopes the general pool, `desired`'s music
    # entries come from a staging dir of symlinks (see
    # resolve_music_folder), not library/music directly, so a raw string
    # comparison against an m3u8 entry (always a real library/music path)
    # would never match even for a track that IS already included.
    music_device_relative_by_source = {
        str(Path(item.source_path).resolve()): item.device_relative_path
        for item in desired.values()
        if item.device_relative_path.startswith(MUSIC_DIRNAME + "/")
    }
    playlists_to_add: dict[str, str] = {}
    playlists_to_update: dict[str, str] = {}
    for pl in profile.playlists:
        m3u8_path = library_root / "playlists" / profile.profile / f"{pl.name}.m3u8"
        if not m3u8_path.is_file():
            continue
        device_relative_tracks: list[str] = []
        for entry in _read_m3u8_entries(m3u8_path):
            entry_path = Path(entry)
            device_relative = music_device_relative_by_source.get(str(entry_path.resolve()))
            if device_relative is None:
                # Not already part of the (possibly scoped) general pool
                # above — a playlist's own tracks are always included
                # regardless of profile.music's scoping, so add it now
                # rather than silently dropping it from the playlist.
                try:
                    device_relative = _add_desired_file(
                        entry_path, music_root, MUSIC_DIRNAME, transcode_options, desired
                    )
                except ValueError:
                    # Not under library/music at all — e.g. a stale m3u8
                    # entry from a source that's since moved. Same
                    # failure mode as iTunes mode silently omitting it
                    # from the on-device playlist; not fatal here either.
                    continue
                music_device_relative_by_source[str(entry_path.resolve())] = device_relative
            device_relative_tracks.append(device_relative)
        playlist_device_relative = f"{PLAYLISTS_DIRNAME}/{pl.name}.m3u8"
        content = _render_m3u8_relative(playlist_device_relative, device_relative_tracks)

        existing_playlist_path = device_root / playlist_device_relative
        if not existing_playlist_path.is_file():
            playlists_to_add[playlist_device_relative] = content
        else:
            try:
                current = existing_playlist_path.read_text()
            except OSError:
                current = None
            if current != content:
                playlists_to_update[playlist_device_relative] = content
        existing.pop(playlist_device_relative, None)

    # Computed only now, after the playlist loop above has had its chance
    # to add any playlist track profile.music's scoping had excluded from
    # `desired` — see the reordering note above _walk_managed_device_files.
    plan = _diff_plan(desired, existing)
    plan.playlists_to_add = playlists_to_add
    plan.playlists_to_update = playlists_to_update

    # Anything left in `existing` that wasn't matched by a playlist above
    # (playlists were already popped out) is a stale playlist or media
    # file no longer in scope.
    desired_keys = set(desired) | {
        f"{PLAYLISTS_DIRNAME}/{pl.name}.m3u8" for pl in profile.playlists
    }
    plan.to_remove = sorted(k for k in existing if k not in desired_keys)
    plan.bytes_to_add = sum(Path(item.source_path).stat().st_size for item in plan.to_add)

    return PlannedRockboxSync(
        plan=plan,
        device_info=device_info,
        snapshot=snapshot,
        before_file_count=len(existing),
        unresolved_selections=unresolved_selections,
        unresolved_audiobook_selections=unresolved_audiobook_selections,
        unresolved_music_selections=unresolved_music_selections,
    )


def _diff_plan(desired: dict[str, RockboxSyncItem], existing: dict[str, float]) -> RockboxSyncPlan:
    plan = RockboxSyncPlan()
    for device_relative, item in desired.items():
        if device_relative not in existing:
            plan.to_add.append(item)
            continue
        source_mtime = Path(item.source_path).stat().st_mtime
        if not _mtime_matches(existing[device_relative], source_mtime):
            plan.to_update.append(item)
    return plan


def _extract_cover_art(source_path: Path) -> tuple[bytes, str] | None:
    """Best-effort read of embedded cover art from a source file, so a
    transcode that strips it (see execute_rockbox_sync) can re-embed the
    original image rather than leaving the device track with no art.
    Returns None (never raises) if the format is unsupported or the file
    has no embedded art — folder-level cover.jpg fallback isn't attempted
    here, matching this project's existing library layout (no per-album
    cover.jpg sidecar for music, only for beets-audible audiobooks, which
    Rockbox will happily show from the folder itself)."""
    suffix = source_path.suffix.lower()
    try:
        if suffix in {".m4a", ".m4b", ".mp4"}:
            from mutagen.mp4 import MP4, MP4Cover

            tags = MP4(source_path).tags
            covr = tags.get("covr") if tags else None
            if not covr:
                return None
            cover = covr[0]
            mime = "image/png" if cover.imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return bytes(cover), mime
        if suffix in {".mp3", ".aac"}:
            from mutagen.id3 import ID3

            id3 = ID3(source_path)
            apics = id3.getall("APIC")
            if not apics:
                return None
            return apics[0].data, apics[0].mime or "image/jpeg"
        if suffix == ".flac":
            from mutagen.flac import FLAC

            pictures = FLAC(source_path).pictures
            if not pictures:
                return None
            return pictures[0].data, pictures[0].mime or "image/jpeg"
    except Exception:
        logger.debug("Could not read embedded art from %s", source_path, exc_info=True)
    return None


def _embed_cover_art(dest_path: Path, art: tuple[bytes, str]) -> None:
    data, mime = art
    suffix = dest_path.suffix.lower()
    try:
        if suffix in {".m4a", ".m4b", ".mp4"}:
            from mutagen.mp4 import MP4, MP4Cover

            mp4 = MP4(dest_path)
            fmt = MP4Cover.FORMAT_PNG if "png" in mime else MP4Cover.FORMAT_JPEG
            mp4.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
            mp4.save()
        elif suffix == ".mp3":
            from mutagen.id3 import ID3, APIC, ID3NoHeaderError

            try:
                id3 = ID3(dest_path)
            except ID3NoHeaderError:
                id3 = ID3()
            id3.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=data))
            id3.save(dest_path, v2_version=3)
    except Exception:
        logger.warning("Could not embed cover art into %s", dest_path, exc_info=True)


def execute_rockbox_sync(
    planned: PlannedRockboxSync, progress_callback: Callable[[str], None] | None = None
) -> dict[str, int]:
    """Executes a previously computed RockboxSyncPlan. Callers must have
    already decided the plan is safe to execute (see cli.py's
    --allow-removals gate on plan.to_remove) — this does not re-check
    removals itself, same contract as sync.execute_sync."""
    device_root = Path(planned.device_info.path)
    plan = planned.plan

    def _write_item(item: RockboxSyncItem) -> None:
        dest = device_root / item.device_relative_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        source_path = Path(item.source_path)

        result = transcode(
            source_path,
            dest.parent,
            output_filename=source_path.stem,
            plan=item.transcode_plan,
        )
        if not result.success or result.output_path is None:
            raise RockboxSyncError(f"transcode failed for {source_path}: {result.error_message}")
        if result.output_path != dest:
            result.output_path.rename(dest)

        # ffmpeg's audio-only transcode commands (-vn) strip any embedded
        # cover art even though basic text tags survive — see the
        # "Key finding" writeup in the Rockbox support plan and
        # notes.md. A plain COPY (no real transcode) already preserves the
        # source file's art byte-for-byte, so only re-embed for tracks
        # that actually went through ffmpeg.
        if item.transcode_plan.target != TranscodeTarget.COPY:
            art = _extract_cover_art(source_path)
            if art is not None:
                _embed_cover_art(dest, art)

        source_mtime = source_path.stat().st_mtime
        os.utime(dest, (source_mtime, source_mtime))
        if progress_callback:
            progress_callback(f"synced {item.device_relative_path}")

    for item in plan.to_add:
        _write_item(item)
    added = len(plan.to_add)
    for item in plan.to_update:
        _write_item(item)
    updated = len(plan.to_update)

    removed = 0
    for device_relative in plan.to_remove:
        target = device_root / device_relative
        try:
            target.unlink()
            removed += 1
        except FileNotFoundError:
            pass
    _prune_empty_dirs(device_root)

    for device_relative, content in {**plan.playlists_to_add, **plan.playlists_to_update}.items():
        dest = device_root / device_relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        if progress_callback:
            progress_callback(f"wrote playlist {device_relative}")

    return {
        "added": added,
        "updated": updated,
        "removed": removed,
        "playlists_written": len(plan.playlists_to_add) + len(plan.playlists_to_update),
    }


def _prune_empty_dirs(device_root: Path) -> None:
    for dirname in _MANAGED_DIRNAMES:
        subroot = device_root / dirname
        if not subroot.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(subroot, topdown=False):
            if not dirnames and not filenames and Path(dirpath) != subroot:
                try:
                    Path(dirpath).rmdir()
                except OSError:
                    pass
