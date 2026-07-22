from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from pathlib import Path

from common.config import ConfigError, load_profile_config
from common.lock import FileLock, LockTimeoutError

from sync_orchestrator.device import DeviceNotFoundError, EjectError, eject_device, find_matching_device
from sync_orchestrator.sync import SyncError, execute_sync, plan_sync


def _format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}m{secs:02d}s"


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def _print_plan(plan) -> None:
    print(
        f"  to_add={len(plan.to_add)} to_remove={len(plan.to_remove)} "
        f"to_update_metadata={len(plan.to_update_metadata)} "
        f"to_update_file={len(plan.to_update_file)} "
        f"to_update_artwork={len(plan.to_update_artwork)}"
    )
    if plan.to_update_artwork:
        print("  artwork changes:")
        for item in plan.to_update_artwork[:10]:
            print(f"    {item.description}")
        if len(plan.to_update_artwork) > 10:
            print(f"    ... and {len(plan.to_update_artwork) - 10} more")
    if plan.duplicates:
        # library-manager's own dedup only scans its own --library-root,
        # with no awareness of other PC folders passed via --pc-folder —
        # this is iopenpod's own fingerprint-based cross-pc_folder
        # duplicate detection (same audio content + same album = true
        # duplicate, one canonical copy kept), the real last line of
        # defense. See notes.md.
        print(f"  duplicates detected across pc_folders ({len(plan.duplicates)} group(s)):")
        for display_key, dupes in list(plan.duplicates.items())[:10]:
            print(f"    {display_key}: {len(dupes)} copies, one kept")
        if len(plan.duplicates) > 10:
            print(f"    ... and {len(plan.duplicates) - 10} more groups")
    print(
        f"  playlists_to_add={len(plan.playlists_to_add)} "
        f"playlists_to_edit={len(plan.playlists_to_edit)} "
        f"playlists_to_remove={len(plan.playlists_to_remove)}"
    )
    for p in plan.playlists_to_add:
        print(f"    + playlist: {p.get('title') or p.get('name') or p}")
    for p in plan.playlists_to_edit:
        print(f"    ~ playlist: {p.get('title') or p.get('name') or p}")
    print(f"  storage: {plan.storage.format()}")
    if plan.to_remove:
        print("  tracks proposed for REMOVAL:")
        for item in plan.to_remove[:20]:
            print(f"    - {item.display_label}")
        if len(plan.to_remove) > 20:
            print(f"    ... and {len(plan.to_remove) - 20} more")
    if plan.to_add:
        print("  sample of tracks proposed for ADDITION:")
        for item in plan.to_add[:10]:
            print(f"    + {item.display_label}")
        if len(plan.to_add) > 10:
            print(f"    ... and {len(plan.to_add) - 10} more")
    if plan.to_update_metadata:
        field_counts: Counter[str] = Counter()
        for item in plan.to_update_metadata:
            field_counts.update(item.metadata_changes.keys())
        print(f"  metadata fields changing (across {len(plan.to_update_metadata)} tracks):")
        for field, count in field_counts.most_common(20):
            print(f"    {field}: {count} tracks")


def _cmd_sync(args: argparse.Namespace) -> int:
    try:
        profile = load_profile_config(args.profile)
    except ConfigError as e:
        print(f"ERROR {args.profile}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    lock_path = Path(args.state_root) / f".sync_{profile.profile}.lock"
    try:
        with FileLock(lock_path, timeout=args.lock_timeout):
            return _run_sync(args, profile)
    except LockTimeoutError as e:
        return _fail(str(e))


def _run_sync(args: argparse.Namespace, profile) -> int:
    start_time = time.monotonic()
    print(f"== Finding device for profile {profile.profile!r} "
          f"({profile.device.match_by}={profile.device.match_value!r}) ==")
    try:
        device_info = find_matching_device(profile.device)
    except DeviceNotFoundError as e:
        return _fail(str(e))
    print(
        f"  {device_info.model_family} {device_info.generation} "
        f"({device_info.model_number}), capacity={device_info.capacity}, "
        f"path={device_info.path}"
    )

    def _report_progress(message: str) -> None:
        print(f"  {message}")

    extra_pc_folders = tuple(args.pc_folders) if args.pc_folders else ()
    try:
        planned = plan_sync(
            device_info=device_info,
            library_root=args.library_root,
            state_root=args.state_root,
            profile=profile,
            extra_pc_folders=extra_pc_folders,
            skip_backup=args.skip_backup,
            skip_podcasts=args.skip_podcasts,
            progress_callback=_report_progress,
        )
    except SyncError as e:
        return _fail(str(e))

    for selection in planned.unresolved_selections:
        print(f"  WARNING: external_library selection {selection!r} matched 0 files")

    if planned.play_states_updated:
        print(
            f"  {planned.play_states_updated} episode(s) with new local play state "
            "recorded (run `podcast-manager push-play-status` to sync to Pocket Casts)"
        )

    print(f"== Plan for {profile.profile!r} ==")
    _print_plan(planned.plan)

    if not args.execute:
        print(
            "\nPLAN ONLY (no --execute passed). Review the numbers above, "
            "especially to_remove, before re-running with --execute."
        )
        print(f"  elapsed: {_format_duration(time.monotonic() - start_time)}")
        return 0

    # A selection that resolves to 0 files is almost certainly a typo'd
    # artist/album/track name (see the WARNING lines above) rather than
    # something genuinely absent — never silently execute a sync that
    # doesn't match what the profile actually asked for.
    if planned.unresolved_selections:
        return _fail(
            f"{len(planned.unresolved_selections)} external_library selection(s) "
            "matched 0 files (see WARNINGs above); refusing to execute until "
            "the profile is fixed"
        )

    # Hard safety gate, not just a printed warning — see
    # docs/m6-ipod-headless-recommendation.md for the near-miss that
    # motivated this: a too-narrow pc_folders list once produced a plan
    # proposing to remove every existing track, and nothing but a human
    # noticing the number stopped it from executing.
    #
    # Removals aren't always a bug though — narrowing an external_library
    # selection (see notes.md) intentionally proposes removing whatever
    # fell out of scope. --allow-removals is the explicit, separate opt-in
    # for that case: --execute alone still refuses on any to_remove, and
    # --allow-removals alone does nothing without --execute.
    if planned.plan.to_remove and not args.allow_removals:
        return _fail(
            f"plan proposes removing {len(planned.plan.to_remove)} track(s); "
            "refusing to execute against a real device without --allow-removals "
            "(review the removal list above first)"
        )

    print("== Executing ==")
    try:
        result, after = execute_sync(planned, progress_callback=_report_progress)
    except SyncError as e:
        return _fail(str(e))

    print(f"  {result.summary}")
    after_count = len(after.get("mhlt", []))
    print(f"  {after_count} tracks now on device (was {planned.before_track_count})")

    snapshot_note = (
        f"Backup snapshot {planned.snapshot.id}"
        if planned.snapshot is not None
        else "The most recent backup snapshot"
    )
    print(
        f"\nPASS: wrote {result.tracks_added} track(s) to a real device. "
        f"{snapshot_note} is available for rollback if needed."
    )

    if not args.skip_eject:
        # A plain filesystem unmount (the previous manual workflow)
        # leaves the USB mass-storage session logically active — the
        # iPod stays in "connected to computer" mode instead of
        # switching to charge-only, unlike what a desktop file manager's
        # eject button actually does (unmount + power off the drive).
        # See device.py/notes.md.
        try:
            eject_device(device_info)
            print("Device ejected — safe to disconnect.")
        except EjectError as e:
            print(f"WARNING: could not eject device automatically: {e}")

    print(f"  elapsed: {_format_duration(time.monotonic() - start_time)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="sync-orchestrator")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync", help="Sync music, playlists, and podcasts to a connected iPod"
    )
    sync_parser.add_argument("--profile", required=True, help="Path to profile YAML")
    sync_parser.add_argument(
        "--library-root",
        required=True,
        help="Host path to the library root (e.g. music-stack/library) — "
        "not global.yaml's paths.library_root, which is a Docker-container "
        "path (/data/library) that doesn't exist on the bare-metal host "
        "this service always runs on.",
    )
    sync_parser.add_argument(
        "--state-root",
        required=True,
        help="Host path to the state root (e.g. music-stack/state), same "
        "reasoning as --library-root.",
    )
    sync_parser.add_argument(
        "--pc-folder",
        dest="pc_folders",
        action="append",
        default=None,
        help="Extra PC media folder to mirror onto the device, beyond "
        "library_root/music and the profile's playlists folder (e.g. a "
        "personal library outside the managed config). Repeatable.",
    )
    sync_parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating a new backup snapshot (only safe if a recent "
        "snapshot already exists and the device hasn't been written to "
        "since).",
    )
    sync_parser.add_argument(
        "--skip-eject",
        action="store_true",
        help="Don't automatically unmount + power off the device after a "
        "successful --execute. By default the device is fully ejected "
        "(not just unmounted) so it actually switches to charge-only mode.",
    )
    sync_parser.add_argument(
        "--skip-podcasts",
        action="store_true",
        help="Don't merge podcast episodes into the plan.",
    )
    sync_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the computed plan. Without this flag, the "
        "plan is computed and printed only — nothing touches the device.",
    )
    sync_parser.add_argument(
        "--allow-removals",
        action="store_true",
        help="Required in addition to --execute whenever the plan proposes "
        "removing tracks (e.g. after narrowing an external_library "
        "selection). Without it, --execute refuses to run on any "
        "to_remove, same as before this flag existed.",
    )
    sync_parser.add_argument(
        "--lock-timeout",
        type=float,
        default=1800,
        help="Max seconds to wait for another sync of this profile to "
        "finish (default 1800).",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
