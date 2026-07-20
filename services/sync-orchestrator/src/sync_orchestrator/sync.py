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
import shutil
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import iopenpod.device as _iopenpod_device
from common.models import ProfileConfig
from common.state import StateDB
from iopenpod.device.info import DeviceInfo, resolve_itdb_path
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

from sync_orchestrator.playstate import resolve_played_states
from sync_orchestrator.selection import build_staging_dir, resolve_selected_files


class SyncError(Exception):
    pass


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


def _load_podcast_feeds(db_path: str, library_root: Path) -> list[PodcastFeed]:
    """Builds PodcastFeed/PodcastEpisode objects directly from
    podcast-manager's own state DB — no file-tag dependency needed (see
    docs/m6-ipod-headless-recommendation.md's podcast section)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT episode_uuid, podcast_uuid, show_name, local_path, "
        "title, audio_url, duration_seconds FROM episodes"
    ).fetchall()
    conn.close()

    feeds_by_show: dict[str, PodcastFeed] = {}
    for row in rows:
        feed = feeds_by_show.get(row["podcast_uuid"])
        if feed is None:
            feed = PodcastFeed(
                feed_url=f"podcast-manager:{row['podcast_uuid']}",
                title=row["show_name"],
            )
            feeds_by_show[row["podcast_uuid"]] = feed

        local_path = Path(row["local_path"])
        if not local_path.is_absolute():
            local_path = library_root / local_path
        feed.episodes.append(
            PodcastEpisode(
                guid=row["episode_uuid"],
                title=row["title"] or Path(row["local_path"]).stem,
                audio_url=row["audio_url"],
                duration_seconds=row["duration_seconds"],
                downloaded_path=str(local_path) if local_path.is_file() else "",
            )
        )

    return list(feeds_by_show.values())


def _capabilities_with_artwork_workaround(info: DeviceInfo) -> Any:
    """This iopenpod version's model tables only have complete entries for
    6th/6.5th/7th-gen "iPod Classic" devices, not e.g. this project's real
    5th/5.5th-gen "iPod Video" — DeviceCapabilities defaults
    supports_artwork=True even for unrecognized families, and
    EngineRequest.device_capabilities doesn't reach the actual write-time
    decision: iopenpod.sync._db_io re-resolves capabilities itself via a
    private in-process device registry. Patch that registry directly so
    the write path sees supports_artwork=False and takes iopenpod's own
    graceful fallback instead of crashing after files are already copied.
    Full writeup: docs/m6-ipod-headless-recommendation.md."""
    capabilities = info.capabilities
    if info.model_family == "iPod Video" and capabilities.cover_art_formats:
        return capabilities
    capabilities = dataclasses.replace(capabilities, supports_artwork=False)
    _iopenpod_device.get_current_device_for_path = lambda path: info
    _iopenpod_device.capabilities_for_family_gen = lambda *a, **kw: capabilities
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
    # Count of local episodes whose play state changed vs. what was
    # already recorded, per resolve_played_states — see playstate.py.
    play_states_updated: int = 0


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

    pc_folders = (
        str(library_root / "music"),
        str(library_root / "playlists" / profile.profile),
        *external_library_folders,
        *extra_pc_folders,
    )
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

    fpcalc_path = shutil.which("fpcalc") or ""
    if not fpcalc_path:
        raise SyncError("fpcalc not found on PATH (chromaprint not installed)")

    capabilities = _capabilities_with_artwork_workaround(device_info)
    storage = _DeviceStorage.from_device_info(device_info)
    options = EngineOptions(
        supports_video=capabilities.supports_video,
        supports_podcast=capabilities.supports_podcast,
        supports_photo=capabilities.supports_photo,
        fpcalc_path=fpcalc_path,
    )

    plan_outcome = SyncEngine().run(
        EngineRequest(
            operation=EngineOperation.PLAN,
            ipod_path=ipod_path,
            pc_folders=pc_folders,
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
            for feed in _load_podcast_feeds(str(state_db_path), library_root):
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
