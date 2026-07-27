from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.backups import (
    RetentionPolicy,
    SnapshotMeta,
    compute_keep_ids,
    prune_and_gc_backups,
    resolve_retention_map,
)
from common.models import (
    AppleMusicSource,
    BackupMaintenanceConfig,
    DeviceMatch,
    GlobalConfig,
    Paths,
    PocketCastsGlobalConfig,
    PodcastsGlobalConfig,
    ProfileBackupRetention,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SourcesConfig,
    SpotifySource,
    SyncSettings,
    YtMusicSource,
)

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def _write_snapshot(
    device_dir: Path, snapshot_id: str, *, timestamp: datetime, device_name: str, hashes: list[str]
) -> None:
    snapshots_dir = device_dir / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 2,
        "id": snapshot_id,
        "timestamp": timestamp.isoformat(),
        "device_id": device_dir.name,
        "device_name": device_name,
        "device_meta": {},
        "file_count": len(hashes),
        "total_size": 0,
        "files": {f"path/{h}": {"hash": h, "size": 0, "mtime_ns": 0} for h in hashes},
    }
    (snapshots_dir / f"{snapshot_id}.json").write_text(json.dumps(data))


def _write_blob(state_root: Path, hash_: str) -> None:
    shard = state_root / "device_backups" / "blobs" / hash_[:2]
    shard.mkdir(parents=True, exist_ok=True)
    (shard / hash_).write_bytes(b"x")


def _global_config(**backups_overrides) -> GlobalConfig:
    return GlobalConfig(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(enabled=True, cookies_file="/x"),
            spotify=SpotifySource(enabled=False, credentials_file="/x"),
            ytmusic=YtMusicSource(enabled=True, oauth_file="/x", cookies_file="/x"),
        ),
        podcasts=PodcastsGlobalConfig(
            pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)
        ),
        backups=BackupMaintenanceConfig(**backups_overrides),
    )


def _profile(
    name: str, match_by: str, match_value: str, *, backups: ProfileBackupRetention | None = None
) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by=match_by, match_value=match_value),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
        backups=backups,
    )


# --- compute_keep_ids ---------------------------------------------------------


def _meta(snapshot_id: str, age_days: float) -> SnapshotMeta:
    return SnapshotMeta(
        device_id="d", snapshot_id=snapshot_id, path=Path("unused"),
        timestamp=NOW - timedelta(days=age_days),
    )


def test_compute_keep_ids_rank_only_keep():
    metas = [_meta("s0", age_days=100)]
    assert compute_keep_ids(metas, RetentionPolicy(keep_last=1, max_age_days=1), NOW) == {"s0"}


def test_compute_keep_ids_age_only_keep():
    metas = [_meta("s0", 1), _meta("s1", 2), _meta("s2", 3)]
    # rank 0 kept by rank; rank 1 (age=2) kept by age (<=2); rank 2 (age=3) fails both
    assert compute_keep_ids(metas, RetentionPolicy(keep_last=1, max_age_days=2), NOW) == {"s0", "s1"}


def test_compute_keep_ids_both_fail_is_pruned():
    metas = [_meta("s0", age_days=100)]
    assert compute_keep_ids(metas, RetentionPolicy(keep_last=0, max_age_days=1), NOW) == set()


def test_compute_keep_ids_boundary_age_equal_to_max_is_kept():
    metas = [_meta("s0", age_days=14)]
    assert compute_keep_ids(metas, RetentionPolicy(keep_last=0, max_age_days=14), NOW) == {"s0"}


# --- resolve_retention_map -----------------------------------------------------


def test_resolve_retention_map_serial_match(tmp_path):
    _write_snapshot(
        tmp_path / "device_backups" / "SERIAL123",
        "20260101_000000", timestamp=NOW, device_name="whatever", hashes=["h1"],
    )
    global_config = _global_config(default_keep_last=3, default_max_age_days=14)
    profile = _profile(
        "alice", "serial", "SERIAL123", backups=ProfileBackupRetention(keep_last=5, max_age_days=30)
    )

    resolution = resolve_retention_map(global_config, [profile], tmp_path)

    assert resolution.by_device_id["SERIAL123"] == RetentionPolicy(keep_last=5, max_age_days=30)
    assert resolution.orphaned_device_ids == []


def test_resolve_retention_map_volume_label_match_samples_newest_snapshot(tmp_path):
    _write_snapshot(
        tmp_path / "device_backups" / "GUID1",
        "20260101_000000", timestamp=NOW, device_name="JOHN'S IPOD", hashes=["h1"],
    )
    global_config = _global_config(default_keep_last=3, default_max_age_days=14)
    profile = _profile("john", "volume_label", "JOHN'S IPOD")

    resolution = resolve_retention_map(global_config, [profile], tmp_path)

    assert resolution.by_device_id["GUID1"] == RetentionPolicy(keep_last=3, max_age_days=14)
    assert resolution.orphaned_device_ids == []


def test_resolve_retention_map_profile_matches_multiple_device_dirs(tmp_path):
    # The real john.yaml case: same physical iPod under two different
    # device_ids across sessions, both must get the profile's policy.
    _write_snapshot(
        tmp_path / "device_backups" / "GUID1",
        "20260101_000000", timestamp=NOW, device_name="JOHN'S IPOD", hashes=["h1"],
    )
    _write_snapshot(
        tmp_path / "device_backups" / "GUID2",
        "20260102_000000", timestamp=NOW, device_name="JOHN'S IPOD", hashes=["h2"],
    )
    global_config = _global_config()
    profile = _profile("john", "volume_label", "JOHN'S IPOD", backups=ProfileBackupRetention(keep_last=7))

    resolution = resolve_retention_map(global_config, [profile], tmp_path)

    assert resolution.by_device_id["GUID1"].keep_last == 7
    assert resolution.by_device_id["GUID2"].keep_last == 7
    assert resolution.orphaned_device_ids == []


def test_resolve_retention_map_orphaned_device_gets_default_policy(tmp_path):
    _write_snapshot(
        tmp_path / "device_backups" / "UNKNOWN",
        "20260101_000000", timestamp=NOW, device_name="Someone Else's iPod", hashes=["h1"],
    )
    global_config = _global_config(default_keep_last=3, default_max_age_days=14)

    resolution = resolve_retention_map(global_config, [], tmp_path)

    assert resolution.by_device_id["UNKNOWN"] == RetentionPolicy(keep_last=3, max_age_days=14)
    assert resolution.orphaned_device_ids == ["UNKNOWN"]


def test_resolve_retention_map_colliding_profiles_merge_to_more_permissive(tmp_path):
    _write_snapshot(
        tmp_path / "device_backups" / "GUID1",
        "20260101_000000", timestamp=NOW, device_name="SHARED IPOD", hashes=["h1"],
    )
    global_config = _global_config()
    profile_a = _profile(
        "alice", "volume_label", "SHARED IPOD", backups=ProfileBackupRetention(keep_last=2, max_age_days=5)
    )
    profile_b = _profile(
        "bob", "volume_label", "SHARED IPOD", backups=ProfileBackupRetention(keep_last=9, max_age_days=1)
    )

    resolution = resolve_retention_map(global_config, [profile_a, profile_b], tmp_path)

    assert resolution.by_device_id["GUID1"] == RetentionPolicy(keep_last=9, max_age_days=5)


# --- prune_and_gc_backups -------------------------------------------------------


def test_prune_and_gc_backups_cross_device_blob_sharing_safety(tmp_path):
    state_root = tmp_path
    shared_hash = "s" * 40
    keep_hash = "k" * 40
    prune_hash = "p" * 40

    device_a = state_root / "device_backups" / "DEVICE_A"
    _write_snapshot(
        device_a, "20260101_000000", timestamp=NOW - timedelta(days=30), device_name="A",
        hashes=[shared_hash, prune_hash],
    )
    _write_snapshot(
        device_a, "20260125_000000", timestamp=NOW - timedelta(days=2), device_name="A",
        hashes=[keep_hash],
    )
    device_b = state_root / "device_backups" / "DEVICE_B"
    _write_snapshot(
        device_b, "20260126_000000", timestamp=NOW - timedelta(days=1), device_name="B",
        hashes=[shared_hash],
    )
    for h in (shared_hash, keep_hash, prune_hash):
        _write_blob(state_root, h)

    policy = RetentionPolicy(keep_last=1, max_age_days=14)
    result = prune_and_gc_backups(
        state_root,
        retention_by_device_id={"DEVICE_A": policy, "DEVICE_B": policy},
        default_retention=policy,
        now=NOW,
    )

    assert result.deleted_snapshots["DEVICE_A"] == ["20260101_000000"]
    assert result.deleted_snapshots["DEVICE_B"] == []
    blobs_dir = state_root / "device_backups" / "blobs"
    assert (blobs_dir / shared_hash[:2] / shared_hash).exists()  # still referenced by device B
    assert (blobs_dir / keep_hash[:2] / keep_hash).exists()  # still referenced by A's survivor
    assert not (blobs_dir / prune_hash[:2] / prune_hash).exists()  # only in the pruned snapshot
    assert result.deleted_blob_count == 1


def test_prune_and_gc_backups_never_touches_hashcache_json(tmp_path):
    state_root = tmp_path
    device_dir = state_root / "device_backups" / "DEVICE_A"
    _write_snapshot(
        device_dir, "20260101_000000", timestamp=NOW - timedelta(days=30), device_name="A",
        hashes=["h1"],
    )
    hashcache = device_dir / "hashcache.json"
    hashcache.write_text("{}")

    policy = RetentionPolicy(keep_last=0, max_age_days=0)
    prune_and_gc_backups(
        state_root, retention_by_device_id={"DEVICE_A": policy}, default_retention=policy, now=NOW
    )

    assert hashcache.exists()


def test_prune_and_gc_backups_dry_run_matches_real_run_but_deletes_nothing(tmp_path):
    state_root = tmp_path
    device_dir = state_root / "device_backups" / "DEVICE_A"
    _write_snapshot(
        device_dir, "20260101_000000", timestamp=NOW - timedelta(days=30), device_name="A",
        hashes=["onlyhash"],
    )
    _write_blob(state_root, "onlyhash")
    policy = RetentionPolicy(keep_last=0, max_age_days=0)

    dry_result = prune_and_gc_backups(
        state_root, retention_by_device_id={"DEVICE_A": policy}, default_retention=policy,
        now=NOW, dry_run=True,
    )

    assert dry_result.deleted_snapshots["DEVICE_A"] == ["20260101_000000"]
    assert dry_result.deleted_blob_count == 1
    assert dry_result.dry_run is True
    assert (device_dir / "snapshots" / "20260101_000000.json").exists()
    assert (state_root / "device_backups" / "blobs" / "on" / "onlyhash").exists()

    real_result = prune_and_gc_backups(
        state_root, retention_by_device_id={"DEVICE_A": policy}, default_retention=policy,
        now=NOW, dry_run=False,
    )

    assert real_result.deleted_snapshots == dry_result.deleted_snapshots
    assert real_result.deleted_blob_count == dry_result.deleted_blob_count
    assert not (device_dir / "snapshots" / "20260101_000000.json").exists()
    assert not (state_root / "device_backups" / "blobs" / "on" / "onlyhash").exists()


def test_prune_and_gc_backups_handles_naive_timestamp_in_real_snapshot_format(tmp_path):
    # Confirmed live against real data: iopenpod's BackupManager writes a
    # naive (no tzinfo) ISO timestamp, e.g. "2026-07-21T08:42:54.422659"
    # — comparing that against an aware `now` used to raise TypeError.
    state_root = tmp_path
    device_dir = state_root / "device_backups" / "DEVICE_A"
    (device_dir / "snapshots").mkdir(parents=True)
    data = {
        "version": 2,
        "id": "20260101_000000",
        "timestamp": "2026-01-01T00:00:00.000000",  # naive, no offset
        "device_id": "DEVICE_A",
        "device_name": "A",
        "device_meta": {},
        "file_count": 1,
        "total_size": 0,
        "files": {"path/h1": {"hash": "h1", "size": 0, "mtime_ns": 0}},
    }
    (device_dir / "snapshots" / "20260101_000000.json").write_text(json.dumps(data))

    policy = RetentionPolicy(keep_last=0, max_age_days=1)
    result = prune_and_gc_backups(
        state_root, retention_by_device_id={"DEVICE_A": policy}, default_retention=policy, now=NOW
    )

    assert result.deleted_snapshots["DEVICE_A"] == ["20260101_000000"]


def test_prune_and_gc_backups_falls_back_to_default_retention_for_unmapped_device(tmp_path):
    state_root = tmp_path
    device_dir = state_root / "device_backups" / "UNMAPPED"
    _write_snapshot(
        device_dir, "20260101_000000", timestamp=NOW - timedelta(days=1), device_name="X",
        hashes=["h1"],
    )
    generous = RetentionPolicy(keep_last=10, max_age_days=90)

    result = prune_and_gc_backups(
        state_root, retention_by_device_id={}, default_retention=generous, now=NOW
    )

    assert result.deleted_snapshots["UNMAPPED"] == []
