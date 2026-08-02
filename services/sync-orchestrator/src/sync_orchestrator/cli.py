from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.config import ConfigError, load_profile_config
from common.lock import FileLock, LockTimeoutError
from common.models import ProfileConfig
from common.schedule import is_due_within, iter_fetch_targets, resolve_fetch_scope
from common.state import StateDB

from sync_orchestrator.device import (
    AmbiguousDeviceMatchError,
    DeviceNotFoundError,
    EjectError,
    eject_device,
    find_matching_device,
    find_matching_profile,
    mount_candidate_devices,
)
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
        print(f"    + playlist: {p.get('Title') or p.get('title') or p.get('name') or p}")
    for p in plan.playlists_to_edit:
        print(f"    ~ playlist: {p.get('Title') or p.get('title') or p.get('name') or p}")
    for p in plan.playlists_to_remove:
        print(f"    - playlist: {p.get('Title') or p.get('title') or p.get('name') or p}")
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
    if args.debug:
        # Surfaces playstate.py's per-track resolution logging (which
        # branch of the device-read-back -> mapping -> local-episode chain
        # a track resolved through, or where it silently dropped out) --
        # see notes.md for the investigation this was added for.
        logging.basicConfig(level=logging.DEBUG, format="%(name)s: %(message)s")

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
    for selection in planned.unresolved_audiobook_selections:
        print(f"  WARNING: audiobooks selection {selection!r} matched 0 files")

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
    if planned.unresolved_audiobook_selections:
        return _fail(
            f"{len(planned.unresolved_audiobook_selections)} audiobooks selection(s) "
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
    # or audiobooks selection (see notes.md) intentionally proposes
    # removing whatever fell out of scope. --allow-removals is the
    # explicit, separate opt-in for that case: --execute alone still
    # refuses on any to_remove, and --allow-removals alone does nothing
    # without --execute.
    #
    # Must also cover plan.playlists_to_remove, not just plan.to_remove
    # (tracks) -- confirmed live these are two separate lists on SyncPlan,
    # and a plain --execute alone would have removed a real on-device
    # playlist with zero review step before this was added. See notes.md.
    if planned.plan.to_remove and not args.allow_removals:
        return _fail(
            f"plan proposes removing {len(planned.plan.to_remove)} track(s); "
            "refusing to execute against a real device without --allow-removals "
            "(review the removal list above first)"
        )
    if planned.plan.playlists_to_remove and not args.allow_removals:
        return _fail(
            f"plan proposes removing {len(planned.plan.playlists_to_remove)} "
            "playlist(s); refusing to execute against a real device without "
            "--allow-removals (review the removal list above first)"
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


def _load_profiles_with_paths(directory: Path) -> list[tuple[Path, ProfileConfig]]:
    """Like common.config.load_all_profiles, but keeps each profile's
    source path alongside it — auto-sync needs the path to invoke
    `music-stack sync --profile <path>` for its pre-fetch subprocess (see
    _maybe_pre_fetch), which load_all_profiles' plain name-keyed dict
    doesn't retain. Re-derives the same fail-fast duplicate-name check
    load_all_profiles does, rather than depending on it directly, so this
    doesn't need to parse every file twice."""
    pairs: list[tuple[Path, ProfileConfig]] = []
    seen: dict[str, Path] = {}
    for path in sorted(directory.glob("*.yaml")):
        profile = load_profile_config(path)
        if profile.profile in seen:
            raise ConfigError(
                path,
                [
                    f"duplicate profile name '{profile.profile}' "
                    f"(already defined in {seen[profile.profile]})"
                ],
            )
        seen[profile.profile] = path
        pairs.append((path, profile))
    return pairs


def _maybe_pre_fetch(
    args: argparse.Namespace,
    profile: ProfileConfig,
    profile_path: Path,
    config_root: Path,
    now: datetime,
) -> None:
    """If any of the matched profile's fetch targets are due within
    --pre-fetch-horizon-hours of right now, pre-fetch just those targets
    before syncing to device — so "plug in before bed" doesn't miss data
    that was about to refresh anyway. Otherwise (the common case) this is
    a no-op: auto-sync syncs whatever fetch-scheduler already put in
    library/, keeping device-plug-in fast.

    Invokes `music-stack sync` as a subprocess rather than importing
    music_stack_cli.orchestrate.run_sync in-process — deliberate: this
    package is kept standalone specifically so its `iopenpod` dependency
    tree never merges with anything else (same reason fetcher-spotify is
    also standalone). music-stack-cli drags in fetcher-apple/gamdl,
    fetcher-ytmusic/yt-dlp, podcast-manager — importing it here would
    relitigate that isolation for the sake of one call.
    """
    state_db_path = Path(args.state_root) / f"{profile.profile}.sqlite"
    horizon = timedelta(hours=args.pre_fetch_horizon_hours)
    targets = iter_fetch_targets(profile)

    with StateDB(state_db_path) as db:
        due_soon = [
            target
            for target in targets
            if is_due_within(
                target.schedule,
                db.get_last_fetched(target.target_type, target.target_id),
                now,
                horizon,
            )
        ]
        if not due_soon:
            return

        scope = resolve_fetch_scope(due_soon)
        print(
            f"== Pre-fetching (due within {args.pre_fetch_horizon_hours}h): "
            f"{', '.join(target.target_id for target in due_soon)} =="
        )
        cmd = [
            "uv", "run", "--project", str(args.music_stack_project_dir),
            "music-stack", "sync",
            "--profile", str(profile_path),
            "--global-config", str(config_root / "global.yaml"),
            "--library-root", str(args.library_root),
            "--state-root", str(args.state_root),
        ]
        for source in sorted(scope.sources):
            cmd += ["--source", source]
        for name in scope.playlist_names or []:
            cmd += ["--playlist", name]
        for name in scope.show_names or []:
            cmd += ["--show", name]

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="")

        # Either way, fall through to the device sync below with whatever's
        # already local — a flaky fetch must never block the "wake up
        # synced" promise from at least syncing what's already there.
        if proc.returncode != 0:
            print(
                f"WARNING: pre-fetch failed (exit {proc.returncode}); syncing with "
                "whatever's already in library/, will retry next scheduled tick"
            )
            return

        for target in due_soon:
            db.record_fetch(target.target_type, target.target_id, now)


def _cmd_auto_sync(args: argparse.Namespace) -> int:
    config_root = Path(args.config_root)
    try:
        profiles_with_paths = _load_profiles_with_paths(config_root / "profiles")
    except ConfigError as e:
        print(f"ERROR {e.path}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    profiles = [profile for _path, profile in profiles_with_paths]
    path_by_name = {profile.profile: path for path, profile in profiles_with_paths}

    print(f"== Waiting up to {args.wait_seconds}s for a known device to connect ==")
    deadline = time.monotonic() + args.wait_seconds
    matched_profile: ProfileConfig | None = None
    last_error: Exception | None = None
    while True:
        # There's no guarantee anything has auto-mounted the device by
        # the time this runs — unlike an interactive `sync` invocation,
        # a udev-triggered run has no desktop session that's necessarily
        # watching for it (confirmed live: udisks2's own auto-mount can
        # simply not happen in this context). Best-effort every poll
        # tick rather than once: the partition may not exist yet on the
        # first tick right after the USB ADD event.
        newly_mounted = mount_candidate_devices()
        for block_device in newly_mounted:
            print(f"  auto-mounted {block_device}")
        try:
            matched_profile = find_matching_profile(profiles)
            break
        except AmbiguousDeviceMatchError as e:
            # A config bug (e.g. two profiles with the same device match)
            # — don't keep polling, surface it immediately.
            return _fail(str(e))
        except DeviceNotFoundError as e:
            last_error = e
        if time.monotonic() >= deadline:
            break
        time.sleep(args.poll_interval)

    if matched_profile is None:
        return _fail(f"no matching profile found within {args.wait_seconds}s: {last_error}")

    print(f"== Matched profile {matched_profile.profile!r} ==")
    now = datetime.now(timezone.utc)
    _maybe_pre_fetch(args, matched_profile, path_by_name[matched_profile.profile], config_root, now)

    # Unattended path: always behaves like a full `sync --execute
    # --allow-removals` run, never a more cautious partial sync — the
    # user explicitly wants "plug in before bed, wake up to everything
    # synced up as configured," including removals. No opt-out flag;
    # add one later if actually wanted, don't build it speculatively now.
    args.execute = True
    args.allow_removals = True

    lock_path = Path(args.state_root) / f".sync_{matched_profile.profile}.lock"
    try:
        with FileLock(lock_path, timeout=args.lock_timeout):
            return _run_sync(args, matched_profile)
    except LockTimeoutError as e:
        return _fail(str(e))


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
    sync_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable DEBUG-level logging, including playstate.py's "
        "per-track device-play-state resolution trace.",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    auto_sync_parser = subparsers.add_parser(
        "auto-sync",
        help="udev-triggered: detect which profile matches whatever iPod "
        "just connected, then sync it. Always executes with removals "
        "allowed (unattended/headless — see README). Never fetches on "
        "its own, except a short pre-fetch window right before a "
        "scheduled fetch would happen anyway.",
    )
    auto_sync_parser.add_argument(
        "--config-root",
        default="config",
        help="Root containing global.yaml and profiles/*.yaml (default 'config').",
    )
    auto_sync_parser.add_argument("--library-root", required=True)
    auto_sync_parser.add_argument("--state-root", required=True)
    auto_sync_parser.add_argument(
        "--wait-seconds",
        type=float,
        default=30,
        help="How long to poll for a known device to appear mounted before "
        "giving up — udev's ADD event fires before udisks/desktop "
        "auto-mount typically finishes (default 30).",
    )
    auto_sync_parser.add_argument("--poll-interval", type=float, default=1.0)
    auto_sync_parser.add_argument(
        "--pre-fetch-horizon-hours",
        type=float,
        default=4.0,
        help="If any of the matched profile's fetch targets are due within "
        "this many hours, pre-fetch just those targets before syncing to "
        "device (default 4).",
    )
    auto_sync_parser.add_argument(
        "--music-stack-project-dir",
        default="services/music-stack-cli",
        help="Path to the music-stack-cli project, used to invoke "
        "`music-stack sync` as a subprocess for the pre-fetch step — kept "
        "out-of-process deliberately (see README): sync-orchestrator stays "
        "isolated from music-stack-cli's heavier dependency tree.",
    )
    auto_sync_parser.add_argument(
        "--pc-folder", dest="pc_folders", action="append", default=None
    )
    auto_sync_parser.add_argument("--skip-backup", action="store_true")
    auto_sync_parser.add_argument("--skip-eject", action="store_true")
    auto_sync_parser.add_argument("--skip-podcasts", action="store_true")
    auto_sync_parser.add_argument("--lock-timeout", type=float, default=1800)
    auto_sync_parser.set_defaults(func=_cmd_auto_sync)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
