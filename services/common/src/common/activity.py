"""Cross-profile activity log + per-profile "last real sync" marker --
backing store for the web GUI's Activity screen and Overview dashboard.
Neither piece existed anywhere in this project before this: every
service that runs today (fetch-scheduler, sync-orchestrator, ...) logs
to stdout or a plain log file, nothing queryable. One shared
`state/activity.sqlite` (not per-profile — the Activity screen shows a
cross-profile feed, and this is the first state file more than one
process writes to around the same moment: fetch-scheduler's tick and a
udev-triggered auto-sync can both fire close together), plus one small
`state/{profile}_last_sync.json` marker per profile."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal


@dataclass
class ActivityEntry:
    started_at: datetime
    service: str
    # A real profile name, or "all" for a cross-profile maintenance task
    # (dedup/cleanup/backup-prune) that isn't scoped to one profile.
    profile: str
    description: str
    duration_seconds: float
    result: Literal["ok", "error"]


def _activity_db_path(state_root: Path | str) -> Path:
    return Path(state_root) / "activity.sqlite"


def _connect(state_root: Path | str) -> sqlite3.Connection:
    path = _activity_db_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # timeout + busy_timeout: fetch-scheduler and a udev-triggered
    # auto-sync are two separate processes that can both want to write
    # here around the same moment -- SQLite's default (fail immediately
    # on a locked db) would raise "database is locked" for a real,
    # ordinary occurrence; a few seconds' wait resolves it instead.
    conn = sqlite3.connect(path, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            service TEXT NOT NULL,
            profile TEXT NOT NULL,
            description TEXT NOT NULL,
            duration_seconds REAL NOT NULL,
            result TEXT NOT NULL
        )
        """
    )
    conn.commit()
    return conn


def record_activity(state_root: Path | str, entry: ActivityEntry) -> None:
    conn = _connect(state_root)
    try:
        conn.execute(
            "INSERT INTO activity "
            "(started_at, service, profile, description, duration_seconds, result) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry.started_at.isoformat(),
                entry.service,
                entry.profile,
                entry.description,
                entry.duration_seconds,
                entry.result,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def list_activity(state_root: Path | str, limit: int = 50) -> list[ActivityEntry]:
    """Newest first. Empty list, not an error, when nothing has ever
    been recorded (the db file doesn't exist yet) -- same "not a
    misconfiguration" treatment this project gives every other
    not-yet-populated state file."""
    path = _activity_db_path(state_root)
    if not path.is_file():
        return []
    conn = _connect(state_root)
    try:
        rows = conn.execute(
            "SELECT started_at, service, profile, description, duration_seconds, result "
            "FROM activity ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        ActivityEntry(
            started_at=datetime.fromisoformat(row[0]),
            service=row[1],
            profile=row[2],
            description=row[3],
            duration_seconds=row[4],
            result=row[5],
        )
        for row in rows
    ]


def prune_activity(state_root: Path | str, older_than_days: int) -> int:
    """Deletes entries older than older_than_days. Returns the number
    deleted. Same shape as common.backups' own retention pruning."""
    path = _activity_db_path(state_root)
    if not path.is_file():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    conn = _connect(state_root)
    try:
        cursor = conn.execute("DELETE FROM activity WHERE started_at < ?", (cutoff.isoformat(),))
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# --- Per-profile "last real sync" marker -----------------------------------
#
# Nothing before this plan recorded "when did this profile's device
# last actually get written to" anywhere queryable -- state/*.sqlite
# tracks per-track/episode bookkeeping, not a sync-run timestamp. A
# plain JSON marker file (same temp-file-then-rename atomicity every
# other credential/config write in this project already uses) is
# simpler than a new table for one timestamp.


def _last_sync_path(state_root: Path | str, profile: str) -> Path:
    return Path(state_root) / f"{profile}_last_sync.json"


def record_last_sync(state_root: Path | str, profile: str, at: datetime) -> None:
    path = _last_sync_path(state_root, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"at": at.isoformat()}), encoding="utf-8")
    tmp.replace(path)


def get_last_sync(state_root: Path | str, profile: str) -> datetime | None:
    path = _last_sync_path(state_root, profile)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return datetime.fromisoformat(data["at"])
    except (json.JSONDecodeError, KeyError, ValueError):
        return None
