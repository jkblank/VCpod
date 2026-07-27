from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from common.models import GlobalConfig, ProfileConfig


@dataclass(frozen=True)
class RetentionPolicy:
    keep_last: int
    max_age_days: int


@dataclass(frozen=True)
class SnapshotMeta:
    device_id: str
    snapshot_id: str  # == filename stem, e.g. "20260721_084254"
    path: Path
    timestamp: datetime
    device_name: str | None = None
    file_hashes: frozenset[str] = field(default=frozenset(), repr=False)


@dataclass
class RetentionResolution:
    by_device_id: dict[str, RetentionPolicy]
    orphaned_device_ids: list[str]  # dirs matched to no profile -> default policy


@dataclass
class PruneResult:
    deleted_snapshots: dict[str, list[str]]  # device_id -> snapshot_ids deleted
    kept_snapshot_counts: dict[str, int]
    deleted_blob_count: int
    deleted_blob_bytes: int
    dry_run: bool


def _device_backups_root(state_root: Path | str) -> Path:
    return Path(state_root) / "device_backups"


def _iter_device_dirs(state_root: Path | str) -> list[Path]:
    root = _device_backups_root(state_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir() and p.name != "blobs")


def _parse_timestamp(raw: str) -> datetime:
    # Confirmed live: iopenpod's BackupManager writes naive (no tzinfo)
    # ISO timestamps, e.g. "2026-07-21T08:42:54.422659" — datetime.now(
    # timezone.utc) - naive would raise TypeError at compute_keep_ids
    # time. Real snapshots are written by a process that only ever deals
    # in UTC internally (confirmed by every other timestamp in this
    # project — state db downloaded_at, fetch_runs.last_fetched_at — all
    # being UTC), so a naive value is treated as UTC, not local time.
    parsed = datetime.fromisoformat(raw)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _read_snapshot(path: Path, device_id: str) -> SnapshotMeta:
    data = json.loads(path.read_text())
    return SnapshotMeta(
        device_id=device_id,
        snapshot_id=path.stem,
        path=path,
        timestamp=_parse_timestamp(data["timestamp"]),
        device_name=data.get("device_name"),
        file_hashes=frozenset(entry["hash"] for entry in data["files"].values()),
    )


def _list_snapshots(device_dir: Path) -> list[SnapshotMeta]:
    """Newest-first. Sorting by filename would also work (snapshot ids are
    YYYYMMDD_HHMMSS, lexicographic order == chronological order) but we
    sort by the parsed timestamp field itself to not depend on that."""
    snapshots_dir = device_dir / "snapshots"
    if not snapshots_dir.is_dir():
        return []
    metas = [
        _read_snapshot(p, device_id=device_dir.name) for p in sorted(snapshots_dir.glob("*.json"))
    ]
    metas.sort(key=lambda m: m.timestamp, reverse=True)
    return metas


def compute_keep_ids(
    metas: list[SnapshotMeta], policy: RetentionPolicy, now: datetime
) -> set[str]:
    """metas must already be newest-first. A snapshot is kept if its
    0-based rank is < policy.keep_last OR its age in days is <=
    policy.max_age_days — i.e. only pruned if it fails BOTH. Conservative
    by construction: even a very tight policy still keeps the most
    recent `keep_last` snapshots regardless of age."""
    keep: set[str] = set()
    for rank, meta in enumerate(metas):
        age_days = (now - meta.timestamp).total_seconds() / 86400
        if rank < policy.keep_last or age_days <= policy.max_age_days:
            keep.add(meta.snapshot_id)
    return keep


def resolve_retention_map(
    global_config: GlobalConfig,
    profiles: Iterable[ProfileConfig],
    state_root: Path | str,
) -> RetentionResolution:
    """Maps each existing state/device_backups/{device_id} directory to a
    RetentionPolicy, without needing a live device connection:

    - device.match_by == "serial": device_id equals profile.device.
      match_value directly (BackupManager's own device_id=serial or
      firewire_guid or profile.profile construction).
    - device.match_by == "volume_label": sample the newest snapshot's
      device_name field and compare to profile.device.match_value (per
      sync.py's device_name=device_info.ipod_name or profile.device.
      match_value construction — device_name IS the configured volume
      label for volume-label-matched profiles).

    Matches ALL device dirs whose sampled name matches a given profile,
    not just the first: confirmed live that the same physical iPod can
    end up under more than one device_id across sessions (a different
    serial/FireWire-GUID read). Two profiles claiming the same device dir
    merge to the MORE PERMISSIVE policy (max of keep_last, max of
    max_age_days) rather than silently picking one — err toward
    retaining more, not less. Any device dir matched by no profile still
    gets the global default policy (never left completely unmanaged),
    and is reported in orphaned_device_ids purely for visibility/logging.
    """
    default_policy = RetentionPolicy(
        keep_last=global_config.backups.default_keep_last,
        max_age_days=global_config.backups.default_max_age_days,
    )

    device_dirs = _iter_device_dirs(state_root)
    device_names: dict[str, str | None] = {}
    for device_dir in device_dirs:
        snapshots = _list_snapshots(device_dir)
        device_names[device_dir.name] = snapshots[0].device_name if snapshots else None

    by_device_id: dict[str, RetentionPolicy] = {}
    matched_device_ids: set[str] = set()

    for profile in profiles:
        policy = RetentionPolicy(
            keep_last=(
                profile.backups.keep_last
                if profile.backups and profile.backups.keep_last
                else default_policy.keep_last
            ),
            max_age_days=(
                profile.backups.max_age_days
                if profile.backups and profile.backups.max_age_days
                else default_policy.max_age_days
            ),
        )

        if profile.device.match_by == "serial":
            candidate_ids = (
                [profile.device.match_value]
                if profile.device.match_value in device_names
                else []
            )
        else:  # volume_label
            candidate_ids = [
                device_id
                for device_id, name in device_names.items()
                if name == profile.device.match_value
            ]

        for device_id in candidate_ids:
            matched_device_ids.add(device_id)
            existing = by_device_id.get(device_id)
            if existing is not None:
                policy = RetentionPolicy(
                    keep_last=max(existing.keep_last, policy.keep_last),
                    max_age_days=max(existing.max_age_days, policy.max_age_days),
                )
            by_device_id[device_id] = policy

    orphaned = [d.name for d in device_dirs if d.name not in matched_device_ids]
    for device_id in orphaned:
        by_device_id[device_id] = default_policy

    return RetentionResolution(by_device_id=by_device_id, orphaned_device_ids=orphaned)


def prune_and_gc_backups(
    state_root: Path | str,
    *,
    retention_by_device_id: dict[str, RetentionPolicy],
    default_retention: RetentionPolicy,
    now: datetime,
    dry_run: bool = False,
) -> PruneResult:
    """Two strict phases, in order:

    1. Decide + delete snapshot manifests, per device, independently —
       any device dir absent from retention_by_device_id falls back to
       default_retention here too (defensive: a caller-side resolution
       bug can never leave a directory completely unmanaged).
    2. Only once every device's keep-set is final, compute the union of
       file hashes referenced by every SURVIVING snapshot across EVERY
       device directory — blobs/ is a single store shared across
       devices, confirmed live (hashes shared between two entirely
       different real device_ids) — and delete any blob file not in that
       union. Never touches hashcache.json: it lives at
       device_backups/{device_id}/hashcache.json, a sibling of
       snapshots/, not under device_backups/blobs/ at all, so the blob
       walk below (scoped to .../blobs/*/*) can't reach it by construction.

    dry_run=True runs identical decision logic; every delete becomes a
    no-op (file stays, counts/bytes still computed) — this doubles as
    the tool for verifying a retention policy against real data before
    ever letting it delete anything for real.
    """
    device_dirs = _iter_device_dirs(state_root)

    deleted_snapshots: dict[str, list[str]] = {}
    kept_snapshot_counts: dict[str, int] = {}
    surviving_hashes: set[str] = set()

    for device_dir in device_dirs:
        device_id = device_dir.name
        policy = retention_by_device_id.get(device_id, default_retention)
        metas = _list_snapshots(device_dir)
        keep_ids = compute_keep_ids(metas, policy, now)

        deleted_for_device: list[str] = []
        for meta in metas:
            if meta.snapshot_id in keep_ids:
                surviving_hashes.update(meta.file_hashes)
            else:
                deleted_for_device.append(meta.snapshot_id)
                if not dry_run:
                    meta.path.unlink()

        deleted_snapshots[device_id] = deleted_for_device
        kept_snapshot_counts[device_id] = len(keep_ids)

    blobs_root = _device_backups_root(state_root) / "blobs"
    deleted_blob_count = 0
    deleted_blob_bytes = 0
    if blobs_root.is_dir():
        for shard_dir in blobs_root.iterdir():
            if not shard_dir.is_dir():
                continue
            for blob_path in shard_dir.iterdir():
                if blob_path.name in surviving_hashes:
                    continue
                size = blob_path.stat().st_size
                deleted_blob_count += 1
                deleted_blob_bytes += size
                if not dry_run:
                    blob_path.unlink()

    return PruneResult(
        deleted_snapshots=deleted_snapshots,
        kept_snapshot_counts=kept_snapshot_counts,
        deleted_blob_count=deleted_blob_count,
        deleted_blob_bytes=deleted_blob_bytes,
        dry_run=dry_run,
    )
