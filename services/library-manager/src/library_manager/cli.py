from __future__ import annotations

import argparse
import sys
from pathlib import Path

from library_manager.cleanup import sweep_quarantine
from library_manager.dedup import find_duplicate_groups, quarantine_duplicates
from library_manager.scan import scan_library


def _cmd_dedup(args: argparse.Namespace) -> int:
    state_dir = Path(args.state_dir)
    state_db_paths = sorted(state_dir.glob("*.sqlite"))
    if not state_db_paths:
        print(f"ERROR: no *.sqlite files found under {state_dir}")
        return 1

    tracks = scan_library(args.library_root)
    groups = find_duplicate_groups(tracks, fuzzy_threshold=args.fuzzy_threshold)

    if not groups:
        print(f"Scanned {len(tracks)} tracks, no cross-source duplicates found.")
        return 0

    result = quarantine_duplicates(
        groups,
        library_root=args.library_root,
        playlists_root=args.playlists_root,
        state_db_paths=state_db_paths,
    )

    print(f"Scanned {len(tracks)} tracks, {len(groups)} duplicate group(s) found.")
    for canonical, (track, dest) in zip(result.canonical, result.quarantined):
        print(f"  kept {canonical.source}:{canonical.source_id} ({canonical.path})")
        print(f"    quarantined {track.source}:{track.source_id} -> {dest}")
    return 0


def _cmd_cleanup_duplicates(args: argparse.Namespace) -> int:
    removed = sweep_quarantine(
        args.library_root, older_than_days=args.older_than_days, dry_run=args.dry_run
    )
    verb = "Would remove" if args.dry_run else "Removed"
    if not removed:
        print(f"{verb} 0 quarantined files (nothing past {args.older_than_days} days).")
        return 0
    print(f"{verb} {len(removed)} quarantined file(s):")
    for path in removed:
        print(f"  {path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="library-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    dedup_parser = subparsers.add_parser(
        "dedup", help="Find and quarantine cross-source duplicate tracks"
    )
    dedup_parser.add_argument("--library-root", required=True)
    dedup_parser.add_argument("--playlists-root", required=True)
    dedup_parser.add_argument(
        "--state-dir", required=True, help="Directory containing per-profile *.sqlite state dbs"
    )
    dedup_parser.add_argument("--fuzzy-threshold", type=float, default=92.0)
    dedup_parser.set_defaults(func=_cmd_dedup)

    cleanup_parser = subparsers.add_parser(
        "cleanup-duplicates", help="Permanently delete old quarantined duplicates"
    )
    cleanup_parser.add_argument("--library-root", required=True)
    cleanup_parser.add_argument("--older-than-days", type=int, default=14)
    cleanup_parser.add_argument("--dry-run", action="store_true")
    cleanup_parser.set_defaults(func=_cmd_cleanup_duplicates)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
