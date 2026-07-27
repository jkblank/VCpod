from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from common.backups import RetentionPolicy, prune_and_gc_backups, resolve_retention_map
from common.config import load_all_profiles, load_global_config
from common.lock import FileLock, LockTimeoutError
from common.models import GlobalConfig, ProfileConfig
from common.schedule import is_due, iter_fetch_targets, resolve_fetch_scope
from common.state import StateDB

from library_manager.cleanup import sweep_quarantine
from library_manager.dedup import find_duplicate_groups, quarantine_duplicates
from library_manager.scan import scan_library

from music_stack_cli.orchestrate import resolve_roots, run_sync

logger = logging.getLogger("fetch_scheduler")


@dataclass
class TickResult:
    # profile name -> target_ids fetched (or, under dry_run, that would be fetched)
    fetched: dict[str, list[str]] = field(default_factory=dict)
    # profile names whose processing raised an unexpected exception this tick
    errors: list[str] = field(default_factory=list)
    # profile name -> run_sync's own source_errors (auth/fetch failures for
    # a due source, e.g. expired cookies) — surfaced separately from
    # `errors` above (unexpected exceptions) so a target that's due every
    # tick but always fails a source auth check isn't silently invisible
    # forever: confirmed live, a profile with a bad/fake playlist id
    # failed every tick with nothing printed anywhere until this was added.
    source_errors: dict[str, list[str]] = field(default_factory=dict)
    # Global (not per-profile) maintenance tasks — library dedup/cleanup,
    # backup pruning. task_id -> outcome summary / exception message.
    maintenance: dict[str, str] = field(default_factory=dict)
    maintenance_errors: dict[str, str] = field(default_factory=dict)


def run_tick(
    *,
    config_root: Path,
    library_root: Path,
    state_root: Path,
    now: datetime,
    dry_run: bool = False,
    lock_timeout: float = 1800,
) -> TickResult:
    """One scheduler pass: for every profile, fetch whatever's due right
    now, tracking completion in that profile's own state db. Each
    profile's processing is isolated in its own try/except — a bug or an
    unexpected exception (anything outside run_sync's own narrowly-caught
    auth exceptions — see its docstring/orchestrate.py) must not stop
    every other profile's fetch this tick."""
    result = TickResult()
    global_config = load_global_config(config_root / "global.yaml")
    profiles = load_all_profiles(config_root / "profiles")

    for profile in profiles.values():
        try:
            _process_profile(
                profile=profile,
                global_config=global_config,
                config_root=config_root,
                library_root=library_root,
                state_root=state_root,
                now=now,
                dry_run=dry_run,
                lock_timeout=lock_timeout,
                result=result,
            )
        except Exception:
            logger.exception("fetch tick failed for profile %r", profile.profile)
            result.errors.append(profile.profile)

    try:
        _process_maintenance(
            global_config=global_config,
            profiles=list(profiles.values()),
            library_root=library_root,
            state_root=state_root,
            now=now,
            dry_run=dry_run,
            lock_timeout=lock_timeout,
            result=result,
        )
    except Exception:
        # Isolated from the per-profile loop above (already returned by
        # this point) for the same reason each profile gets its own
        # try/except — one broken thing must not take down the rest.
        logger.exception("maintenance processing failed")

    return result


def _process_profile(
    *,
    profile: ProfileConfig,
    global_config,
    config_root: Path,
    library_root: Path,
    state_root: Path,
    now: datetime,
    dry_run: bool,
    lock_timeout: float,
    result: TickResult,
) -> None:
    roots = resolve_roots(library_root, state_root, profile.profile)
    targets = iter_fetch_targets(profile)

    with StateDB(roots.state_db_path) as db:
        due = [
            target
            for target in targets
            if is_due(target.schedule, db.get_last_fetched(target.target_type, target.target_id), now)
        ]
        if not due:
            return

        if dry_run:
            result.fetched[profile.profile] = [target.target_id for target in due]
            return

        scope = resolve_fetch_scope(due)
        lock_path = roots.state_db_path.parent / f".fetch_{profile.profile}.lock"
        try:
            with FileLock(lock_path, timeout=lock_timeout):
                sync_result = run_sync(
                    profile=profile,
                    global_config=global_config,
                    config_root=config_root,
                    roots=roots,
                    sources=scope.sources,
                    playlist_names=scope.playlist_names,
                    show_selectors=scope.show_names,
                    lock_timeout=lock_timeout,
                )
        except LockTimeoutError as e:
            logger.warning("skipping %r this tick (locked): %s", profile.profile, e)
            return

        if sync_result.source_errors:
            result.source_errors[profile.profile] = sync_result.source_errors

        # A source's own auth/fetch failure (surfaced in source_errors, not
        # raised) must not be recorded as fetched — leave it due so the
        # next tick retries it, rather than silently marking a failed
        # fetch as done.
        failed_sources = {error.split(":", 1)[0].strip() for error in sync_result.source_errors}
        fetched_ids = []
        for target in due:
            target_source = target.source if target.target_type == "playlist" else "podcasts"
            if target_source in failed_sources:
                continue
            db.record_fetch(target.target_type, target.target_id, now)
            fetched_ids.append(target.target_id)
        result.fetched[profile.profile] = fetched_ids


def _process_maintenance(
    *,
    global_config: GlobalConfig,
    profiles: list[ProfileConfig],
    library_root: Path,
    state_root: Path,
    now: datetime,
    dry_run: bool,
    lock_timeout: float,
    result: TickResult,
) -> None:
    """Global (not per-profile) maintenance: library dedup/cleanup and
    backup pruning. No schedule of their own — runs as a post-step
    whenever any profile actually fetched (or, under dry_run, would have
    fetched) this tick, i.e. `result.fetched` is non-empty, gated per-task
    by its own *_enabled flag rather than a separate cron. Runs at most
    once per tick regardless of how many profiles fetched — these are
    cross-profile/global operations, not per-profile ones."""
    if not result.fetched:
        return

    tasks = (
        (
            "library_dedup",
            global_config.library_manager.dedup_enabled,
            lambda: _run_library_dedup(
                library_root=library_root,
                state_root=state_root,
                fuzzy_threshold=global_config.library_manager.fuzzy_threshold,
                dry_run=dry_run,
            ),
        ),
        (
            "library_cleanup",
            global_config.library_manager.cleanup_enabled,
            lambda: _run_library_cleanup(
                library_root=library_root,
                older_than_days=global_config.library_manager.quarantine_older_than_days,
                dry_run=dry_run,
            ),
        ),
        (
            "backup_prune",
            global_config.backups.prune_enabled,
            lambda: _run_backup_prune(
                global_config=global_config,
                profiles=profiles,
                state_root=state_root,
                now=now,
                dry_run=dry_run,
            ),
        ),
    )

    enabled_tasks = [(task_id, runner) for task_id, enabled, runner in tasks if enabled]
    if not enabled_tasks:
        return

    lock_path = state_root / ".maintenance.lock"
    try:
        with FileLock(lock_path, timeout=lock_timeout):
            for task_id, runner in enabled_tasks:
                try:
                    result.maintenance[task_id] = runner()
                except Exception:
                    logger.exception("maintenance task %r failed", task_id)
                    result.maintenance_errors[task_id] = "task failed, see log"
    except LockTimeoutError as e:
        logger.warning("skipping maintenance this tick (locked): %s", e)


def _run_library_dedup(
    *, library_root: Path, state_root: Path, fuzzy_threshold: float, dry_run: bool
) -> str:
    music_root = library_root / "music"
    playlists_root = library_root / "playlists"
    # Excludes "global.sqlite" defensively — library-manager's own CLI
    # globs every *.sqlite under state_root expecting one file per
    # profile, and "global" is a reserved profile name (see
    # common.config.load_profile_config) specifically to keep that
    # filename free for any future non-profile-scoped state.
    state_db_paths = sorted(p for p in state_root.glob("*.sqlite") if p.name != "global.sqlite")

    tracks = scan_library(music_root)
    groups = find_duplicate_groups(tracks, fuzzy_threshold=fuzzy_threshold)

    if not groups:
        return f"scanned {len(tracks)} tracks, no cross-source duplicates found"
    if dry_run:
        # quarantine_duplicates has no read-only mode — same "don't call
        # the mutating function" convention run_sync's own dry-run uses.
        return f"scanned {len(tracks)} tracks, {len(groups)} duplicate group(s) would be quarantined"

    dedup_result = quarantine_duplicates(
        groups, library_root=music_root, playlists_root=playlists_root, state_db_paths=state_db_paths
    )
    return f"scanned {len(tracks)} tracks, quarantined {len(dedup_result.quarantined)} duplicate(s)"


def _run_library_cleanup(*, library_root: Path, older_than_days: int, dry_run: bool) -> str:
    # sweep_quarantine already has a real dry-run mode — called for real
    # either way, unlike dedup above.
    removed = sweep_quarantine(library_root / "music", older_than_days=older_than_days, dry_run=dry_run)
    verb = "would remove" if dry_run else "removed"
    return f"{verb} {len(removed)} quarantined file(s) older than {older_than_days} days"


def _run_backup_prune(
    *,
    global_config: GlobalConfig,
    profiles: list[ProfileConfig],
    state_root: Path,
    now: datetime,
    dry_run: bool,
) -> str:
    resolution = resolve_retention_map(global_config, profiles, state_root)
    default_retention = RetentionPolicy(
        keep_last=global_config.backups.default_keep_last,
        max_age_days=global_config.backups.default_max_age_days,
    )
    prune_result = prune_and_gc_backups(
        state_root,
        retention_by_device_id=resolution.by_device_id,
        default_retention=default_retention,
        now=now,
        dry_run=dry_run,
    )
    deleted_snapshot_count = sum(len(ids) for ids in prune_result.deleted_snapshots.values())
    verb = "would delete" if dry_run else "deleted"
    return (
        f"{verb} {deleted_snapshot_count} snapshot(s) and {prune_result.deleted_blob_count} "
        f"blob(s) ({prune_result.deleted_blob_bytes} bytes)"
    )
