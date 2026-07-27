from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

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


def _run_once(args: argparse.Namespace) -> int:
    result = run_tick(
        config_root=args.config_root,
        library_root=args.library_root,
        state_root=args.state_root,
        now=datetime.now(timezone.utc),
        dry_run=args.dry_run,
        lock_timeout=args.lock_timeout,
    )
    _print_tick_result(result)
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
