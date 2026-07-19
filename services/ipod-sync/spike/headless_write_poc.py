"""M6 proof-of-concept: sync real PC music folders to a real iPod using
iOpenPod's sync core directly, without launching its PyQt6 GUI.

Deliberately avoids importing anything from `iopenpod.application` or
`iopenpod.gui` — the `application` package's own `__init__.py` eagerly
imports QThread-based worker classes (see
docs/m6-ipod-headless-recommendation.md), so touching any name under
`iopenpod.application.*` pulls PyQt6 into `sys.modules` even though a
handful of individual files in that package are themselves Qt-free. The
`device`, `itunesdb_parser`, and `sync.core` packages used below have no
such leak: they can be imported and used standalone.

SyncEngine's PLAN treats the given `pc_folders` as the *complete*
authoritative source for what should be on the device — anything on the
device not found there gets proposed for removal. Point this at every PC
folder that's supposed to be mirrored (the original library plus any newer
downloaded library), not a narrow scratch folder, or PLAN will propose
removing everything else.

Two-phase by design: this always computes and prints the plan. It will
only execute the plan if you pass --execute, so the plan can be reviewed
against a real, large personal library before anything is written.

Usage:
    uv run spike/headless_write_poc.py                  # plan only
    uv run spike/headless_write_poc.py --execute         # plan + write
"""

from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from iopenpod.device.info import DeviceInfo, enrich, resolve_itdb_path
from iopenpod.itunesdb_parser.ipod_library import load_ipod_library
from iopenpod.podcasts.models import PodcastEpisode, PodcastFeed
from iopenpod.podcasts.podcast_sync import build_podcast_sync_plan
from iopenpod.sync.backup_manager import BackupManager
from iopenpod.sync.core.engine import SyncEngine
from iopenpod.sync.core.models import EngineOperation, EngineOptions, EngineRequest

DEFAULT_IPOD_PATH = "/run/media/john/JOHN_S IPOD"
DEFAULT_PC_FOLDERS = (
    "/home/john/Music/MusicLibrary",
    "/home/john/Music/music-stack/library/music",
    # .m3u8 playlist files are discovered by scanning pc_folders for them
    # (sync/sync_playlist_files.py) — a bare folder entry's default
    # media_types already includes "playlists", so no special tagging is
    # needed, but the folder itself must be in this list or playlists are
    # never even looked at. Missing this the first time around meant the
    # M6 full-library sync added/updated every track correctly but never
    # touched playlists at all.
    "/home/john/Music/music-stack/library/playlists/john",
)
DEFAULT_PODCAST_STATE_DB = "/home/john/Music/music-stack/state/john.sqlite"
MUSIC_STACK_ROOT = Path("/home/john/Music/music-stack")


def _load_podcast_feeds(db_path: str) -> list[PodcastFeed]:
    """Build PodcastFeed/PodcastEpisode objects directly from
    podcast-manager's own state DB — no file-tag dependency, per
    docs/m6-ipod-headless-recommendation.md's podcast section. Mirrors
    spike/podcast_prototype.py, now using the title/audio_url/
    duration_seconds fields that were missing when that prototype was
    first written (see notes.md)."""

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
            local_path = MUSIC_STACK_ROOT / local_path
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


@dataclass(frozen=True)
class _DeviceStorage:
    """Minimal stand-in for application.services.DeviceStorageSnapshot,
    built straight from DeviceInfo fields so this script never has to
    import the application package (see module docstring)."""

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


def _fail(message: str) -> int:
    print(f"FAIL: {message}")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipod-path", default=DEFAULT_IPOD_PATH)
    parser.add_argument(
        "--pc-folder",
        dest="pc_folders",
        action="append",
        default=None,
        help="PC media folder to mirror onto the device. Repeatable. "
        f"Defaults to both: {', '.join(DEFAULT_PC_FOLDERS)}",
    )
    parser.add_argument(
        "--backup-dir", default=str(Path(__file__).parent / "_backups")
    )
    parser.add_argument(
        "--skip-backup",
        action="store_true",
        help="Skip creating a new backup snapshot (only safe if a recent "
        "snapshot already exists and the device hasn't been written to "
        "since).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually write the computed plan. Without this flag, the "
        "plan is computed and printed only — nothing touches the device.",
    )
    parser.add_argument("--podcast-state-db", default=DEFAULT_PODCAST_STATE_DB)
    parser.add_argument(
        "--skip-podcasts",
        action="store_true",
        help="Don't merge podcast episodes into the plan.",
    )
    args = parser.parse_args()

    ipod_path = args.ipod_path
    pc_folders = tuple(args.pc_folders) if args.pc_folders else DEFAULT_PC_FOLDERS
    for folder in pc_folders:
        if not Path(folder).is_dir():
            return _fail(f"pc folder not found: {folder}")

    itunesdb_path = resolve_itdb_path(ipod_path)
    if not itunesdb_path:
        return _fail(f"could not resolve iTunesDB path under {ipod_path}")

    print(f"== Identifying device at {ipod_path} ==")
    info = DeviceInfo(path=ipod_path)
    enrich(info)
    print(
        f"  {info.model_family} {info.generation} ({info.model_number}), "
        f"capacity={info.capacity}, fs={info.filesystem_type}, "
        f"serial={info.serial or info.firewire_guid or '?'}"
    )

    backup_mgr = BackupManager(
        device_id=info.serial or info.firewire_guid or "john_ipod",
        backup_dir=args.backup_dir,
        device_name=info.ipod_name or "JOHN'S IPOD",
    )
    if args.skip_backup:
        print("== Skipping backup (--skip-backup): reusing most recent snapshot ==")
        snapshot = True  # sentinel: only used below for the None-check/print
    else:
        print("== Creating safety backup (BackupManager.create_backup) ==")
        snapshot = backup_mgr.create_backup(
            ipod_path,
            reported_volume_format=info.reported_volume_format,
            expected_volume_identity_key=info.volume_identity_key,
        )
        if snapshot is None:
            return _fail("backup did not produce a snapshot; refusing to write")
        print(
            f"  snapshot {snapshot.id}: {snapshot.file_count} files, "
            f"{snapshot.total_size} bytes -> {args.backup_dir}"
        )

    print("== Loading existing on-device library ==")
    before = load_ipod_library(itunesdb_path)
    if before is None:
        return _fail("could not parse iTunesDB")
    before_tracks = before.get("mhlt", [])
    before_playlists = before.get("mhlp", [])
    before_count = len(before_tracks)
    print(f"  {before_count} existing tracks, {len(before_playlists)} playlists")

    fpcalc_path = shutil.which("fpcalc") or ""
    if not fpcalc_path:
        return _fail("fpcalc not found on PATH (chromaprint not installed)")

    capabilities = info.capabilities
    if info.model_family != "iPod Video" or not capabilities.cover_art_formats:
        # This device (confirmed via lsusb and the device's own SysInfo:
        # "ModelFamily: iPod Video") is a 5th/5.5th-gen iPod Video. This
        # iopenpod version's model tables (device/models.py) only have
        # complete entries for 6th/6.5th/7th-gen "iPod Classic" — 0x1209 is
        # deliberately mapped to a coarse ("iPod", "") placeholder with no
        # cover_art_formats. Passing supports_artwork=False through
        # EngineRequest.device_capabilities does NOT reach the actual
        # artwork decision: iopenpod.sync._db_io.write_database ignores it
        # and instead re-resolves capabilities itself via
        # iopenpod.device.get_current_device_for_path() (a private
        # in-process device registry we never populate) and
        # capabilities_for_family_gen() (a static lookup table with no
        # "iPod Video" entry) — confirmed by reading both functions after
        # the EngineRequest-level override had no effect on a real run.
        # Patch those two entry points directly so write_database's
        # internal resolution sees supports_artwork=False, rather than
        # letting it silently resolve to a capabilities object (or None)
        # that leaves artwork_formats unset and crashes after files are
        # already copied. See docs/m6-ipod-headless-recommendation.md.
        import dataclasses

        import iopenpod.device as _iopenpod_device

        print(
            "  NOTE: disabling artwork writes — this iopenpod version has "
            "no cover art format data for iPod Video (5/5.5G); only "
            f"iPod Classic (6/6.5/7G) is fully modeled. Detected identity: "
            f"family={info.model_family!r} generation={info.generation!r}"
        )
        capabilities = dataclasses.replace(capabilities, supports_artwork=False)
        _iopenpod_device.get_current_device_for_path = lambda path: info
        _iopenpod_device.capabilities_for_family_gen = (
            lambda *a, **kw: capabilities
        )
    storage = _DeviceStorage.from_device_info(info)
    options = EngineOptions(
        supports_video=capabilities.supports_video,
        supports_podcast=capabilities.supports_podcast,
        supports_photo=capabilities.supports_photo,
        fpcalc_path=fpcalc_path,
    )

    print(f"== SyncEngine: PLAN against {len(pc_folders)} pc folder(s) ==")
    for folder in pc_folders:
        print(f"  {folder}")
    plan_outcome = SyncEngine().run(
        EngineRequest(
            operation=EngineOperation.PLAN,
            ipod_path=ipod_path,
            pc_folders=pc_folders,
            ipod_tracks=tuple(before_tracks),
            existing_playlists=tuple(before_playlists),
            options=options,
            device_info=info,
            device_capabilities=capabilities,
            device_storage=storage,
        )
    )

    # iopenpod's FingerprintCache.save() (sync/audio_fingerprint.py) is
    # only ever called right after PC-side library scanning finishes —
    # there's no equivalent save after device-side track fingerprinting
    # (fingerprint_diff_engine.py's _ipod_track_fingerprint_index, which
    # runs later in the same compute_diff call). cache.store() does get
    # called correctly for every device track, so the data exists in this
    # process's memory the whole time — it's just never flushed to disk,
    # meaning every separate run re-fingerprints the entire on-device
    # library from scratch over USB (confirmed live: ~50-55 min every
    # single time, PC-side hits cache instantly on repeat runs but device
    # side never does). Force a save here so this run's device-side work
    # isn't wasted — every run after this one should see real cache hits
    # on the device side too. See docs/m6-ipod-headless-recommendation.md.
    from iopenpod.sync.audio_fingerprint import FingerprintCache

    FingerprintCache.get_instance().save()

    if not plan_outcome.success:
        for d in plan_outcome.diagnostics:
            print(f"  [{d.stage}] {d.code}: {d.message}")
        return _fail("planning failed")

    plan = plan_outcome.result

    if not args.skip_podcasts:
        print(f"== Building podcast plan from {args.podcast_state_db} ==")
        feeds = _load_podcast_feeds(args.podcast_state_db)
        podcast_additions = 0
        podcast_bytes = 0
        for feed in feeds:
            episode_feed_pairs = [
                (ep, feed) for ep in feed.episodes if ep.downloaded_path
            ]
            if not episode_feed_pairs:
                continue
            podcast_plan = build_podcast_sync_plan(episode_feed_pairs, before_tracks)
            if not podcast_plan.to_add:
                continue
            print(
                f"  {feed.title}: {len(podcast_plan.to_add)} new episode(s), "
                f"{podcast_plan.storage.format()}"
            )
            # Same merge approach the real app uses (application/
            # sync_session.py): extend to_add, add up storage. Everything
            # else (mapping, integrity_report, playlists_to_add, ...)
            # stays from the music/playlist plan — build_podcast_sync_plan
            # never sets those fields.
            plan.to_add.extend(podcast_plan.to_add)
            plan.storage.bytes_to_add += podcast_plan.storage.bytes_to_add
            podcast_additions += len(podcast_plan.to_add)
            podcast_bytes += podcast_plan.storage.bytes_to_add
        print(
            f"  total: {podcast_additions} new episode(s) across "
            f"{len(feeds)} feed(s), +{podcast_bytes / 1e9:.2f} GB"
        )

    print(
        f"  to_add={len(plan.to_add)} to_remove={len(plan.to_remove)} "
        f"to_update_metadata={len(plan.to_update_metadata)} "
        f"to_update_file={len(plan.to_update_file)}"
    )
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
        from collections import Counter

        field_counts: Counter[str] = Counter()
        for item in plan.to_update_metadata:
            field_counts.update(item.metadata_changes.keys())
        print(f"  metadata fields changing (across {len(plan.to_update_metadata)} tracks):")
        for field, count in field_counts.most_common(20):
            print(f"    {field}: {count} tracks")
        print("  sample of individual metadata changes:")
        for item in plan.to_update_metadata[:5]:
            print(f"    ~ {item.display_label}: {item.metadata_changes}")

    if not args.execute:
        print(
            "\nPLAN ONLY (no --execute passed). Review the numbers above, "
            "especially to_remove, before re-running with --execute."
        )
        return 0

    # Hard safety gate, not just a printed warning: this combined
    # music+playlists+podcasts plan is new, not-yet-live-tested
    # integration code. The music/playlist portion was already verified
    # clean in an earlier separate run (to_remove=0), so any removal
    # showing up now is unexpected — refuse rather than trust it blindly.
    # This mirrors the exact near-miss earlier in M6 where a
    # too-narrow pc_folders list produced a plan proposing to remove
    # every existing track; the script back then had no such gate and a
    # human had to notice the number.
    if plan.to_remove:
        return _fail(
            f"plan unexpectedly proposes removing {len(plan.to_remove)} track(s); "
            "refusing to execute against a real device"
        )

    print("== SyncEngine: EXECUTE ==")
    exec_outcome = SyncEngine().run(
        EngineRequest(
            operation=EngineOperation.EXECUTE,
            ipod_path=ipod_path,
            plan=plan,
            options=options,
            device_info=info,
            device_capabilities=capabilities,
            device_storage=storage,
        )
    )
    result = exec_outcome.result
    if not exec_outcome.success or (result is not None and result.has_errors):
        for d in exec_outcome.diagnostics:
            print(f"  [{d.stage}] {d.code}: {d.message}")
        if result is not None:
            for stage, message in result.errors:
                print(f"  [{stage}] {message}")
        return _fail("execution failed")
    print(f"  {result.summary}")

    print("== Re-reading iTunesDB to verify ==")
    after = load_ipod_library(itunesdb_path)
    if after is None:
        return _fail("could not re-parse iTunesDB after write")
    after_tracks = after.get("mhlt", [])
    after_count = len(after_tracks)
    print(f"  {after_count} tracks now on device (was {before_count})")

    snapshot_note = (
        f"Backup snapshot {snapshot.id}" if snapshot is not True
        else "The most recent backup snapshot"
    )
    print(
        f"\nPASS: wrote {result.tracks_added} track(s) to a real device "
        f"without the GUI. {snapshot_note} is available for rollback via "
        f"BackupManager.restore_backup() if needed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
