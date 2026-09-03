from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from common.activity import ActivityEntry, record_activity

from fetch_scheduler.loop import TickResult, run_tick


def _print_tick_result(result: TickResult) -> None:
    for profile, target_ids in result.fetched.items():
        if target_ids:
            print(f"[{profile}] fetched: {', '.join(target_ids)}")
    for profile, errors in result.source_errors.items():
        for error in errors:
            print(f"[{profile}] ERROR: {error}")
    for profile in result.errors:
        print(f"[{profile}] ERROR: tick failed, see log")
    for task_id, summary in result.maintenance.items():
        print(f"[maintenance:{task_id}] {summary}")
    for task_id in result.maintenance_errors:
        print(f"[maintenance:{task_id}] ERROR: task failed, see log")
    if (
        not result.fetched
        and not result.errors
        and not result.source_errors
        and not result.maintenance
        and not result.maintenance_errors
    ):
        print("nothing due")


def _record_tick_activity(state_root: Path, result: TickResult, duration_seconds: float) -> None:
    # No per-profile/per-task timing exists inside TickResult -- the whole
    # tick's wall time is recorded against every entry from it, a coarse
    # but honest approximation rather than a fabricated per-item split.
    started_at = datetime.now(timezone.utc)

    active_profiles = set(result.errors) | set(result.source_errors)
    active_profiles |= {profile for profile, ids in result.fetched.items() if ids}

    for profile in active_profiles:
        if profile in result.errors:
            description = "fetch tick — unexpected error, see log"
            outcome = "error"
        else:
            parts = []
            fetched_ids = result.fetched.get(profile) or []
            if fetched_ids:
                parts.append(f"fetched: {', '.join(fetched_ids)}")
            source_errors = result.source_errors.get(profile) or []
            if source_errors:
                parts.append(f"errors: {'; '.join(source_errors)}")
            description = "fetch tick — " + "; ".join(parts)
            outcome = "error" if source_errors else "ok"

        record_activity(
            state_root,
            ActivityEntry(
                started_at=started_at,
                service="fetch-scheduler",
                profile=profile,
                description=description,
                duration_seconds=duration_seconds,
                result=outcome,
            ),
        )

    for task_id, summary in result.maintenance.items():
        record_activity(
            state_root,
            ActivityEntry(
                started_at=started_at,
                service="fetch-scheduler",
                profile="all",
                description=f"maintenance:{task_id} — {summary}",
                duration_seconds=duration_seconds,
                result="ok",
            ),
        )
    for task_id in result.maintenance_errors:
        record_activity(
            state_root,
            ActivityEntry(
                started_at=started_at,
                service="fetch-scheduler",
                profile="all",
                description=f"maintenance:{task_id} — task failed, see log",
                duration_seconds=duration_seconds,
                result="error",
            ),
        )


def _run_once(args: argparse.Namespace) -> int:
    tick_started = time.monotonic()
    result = run_tick(
        config_root=args.config_root,
        library_root=args.library_root,
        state_root=args.state_root,
        now=datetime.now(timezone.utc),
        dry_run=args.dry_run,
        lock_timeout=args.lock_timeout,
    )
    _print_tick_result(result)
    # dry_run's `result.fetched` lists what *would* be fetched, not real
    # activity -- recording it as a real entry would be a fabricated log.
    if not args.dry_run:
        _record_tick_activity(args.state_root, result, time.monotonic() - tick_started)
    return 1 if (result.errors or result.maintenance_errors) else 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="fetch-scheduler")
    parser.add_argument("--config-root", type=Path, default=Path("config"))
    parser.add_argument(
        "--library-root",
        type=Path,
        default=None,
        help="Defaults to a 'library' directory next to --config-root.",
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=None,
        help="Defaults to a 'state' directory next to --config-root.",
    )
    parser.add_argument(
        "--tick-seconds",
        type=int,
        default=60,
        help="Seconds between ticks when run as a long-lived process (ignored with --once).",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick and exit, instead of looping forever. Useful "
        "for cron/systemd-timer-driven invocation instead of a long-running "
        "process, and for manual verification.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print due targets without calling any fetcher or writing fetch_runs.",
    )
    parser.add_argument("--lock-timeout", type=float, default=1800)
    args = parser.parse_args()

    if args.library_root is None:
        args.library_root = args.config_root.parent / "library"
    if args.state_root is None:
        args.state_root = args.config_root.parent / "state"

    if args.once:
        sys.exit(_run_once(args))

    while True:
        _run_once(args)
        time.sleep(args.tick_seconds)


if __name__ == "__main__":
    main()
