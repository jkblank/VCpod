from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from common.config import load_all_profiles, load_global_config
from common.lock import FileLock, LockTimeoutError
from common.models import ProfileConfig
from common.schedule import is_due, iter_fetch_targets, resolve_fetch_scope
from common.state import StateDB

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
