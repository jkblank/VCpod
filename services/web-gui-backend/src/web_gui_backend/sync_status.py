"""Is a real sync currently running for a given profile -- right now,
regardless of who started it.

sync-orchestrator's `sync`/`full-sync`/`auto-sync` commands all take the
exact same `state/.sync_{profile}.lock` (POSIX advisory `flock`, see
common.lock.FileLock) before touching a device, so that lock's state
*is* "is a sync in progress" -- the same fact sync-orchestrator itself
relies on to keep two of its own invocations from overlapping. Checking
it here is a non-blocking probe-and-immediately-release: opening the
file and attempting `flock(LOCK_EX | LOCK_NB)` either succeeds (nobody
else holds it -- release again immediately) or raises `BlockingIOError`
(someone does). Neither outcome can contend with, delay, or otherwise
touch whatever process actually holds the lock -- verified live against
a real in-progress sync before this was built.

This is also the *only* way to see a headless auto-sync at all: a
`music-stack-auto-sync.service` run (systemd, udev-triggered) never
goes through this backend's `/api/sync/execute` -- it invokes
sync-orchestrator directly as its own subprocess, so there is no HTTP
request to attach to. Its progress lines (stdout+stderr) land in
`state/auto-sync.log`, the one place they're captured anywhere -- see
routers/auto_sync_setup.py's generated systemd unit."""

from __future__ import annotations

import fcntl
from pathlib import Path

# How stale auto-sync.log's mtime can be while still being shown
# alongside a "running: true" result. Without this, a months-old log
# left over from a past run could be mistaken for live progress of
# whatever's holding the lock right now (e.g. a sync triggered from the
# web GUI, which never writes to this file at all). Generous on purpose
# -- a real first sync of a large library can run for a long time
# without a lock-holder's own long-running steps producing fresh output.
_LOG_FRESH_WITHIN_SECONDS = 2 * 60 * 60

_LOG_TAIL_LINES = 20


def _lock_path(state_root: Path, profile: str) -> Path:
    return state_root / f".sync_{profile}.lock"


def is_sync_running(state_root: Path, profile: str) -> bool:
    path = _lock_path(state_root, profile)
    if not path.is_file():
        # No lock file at all means no sync has ever run for this
        # profile -- and, importantly, means this check never creates
        # one: a read-only status probe shouldn't have a filesystem
        # side effect just because nothing has happened yet (same
        # "no file yet = nothing happened yet, not an error or a write"
        # convention common.activity.get_last_sync already uses).
        return False
    fd = open(path, "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        fd.close()


def recent_auto_sync_log_tail(state_root: Path, *, now: float) -> list[str] | None:
    """Last `_LOG_TAIL_LINES` lines of state/auto-sync.log, or None if
    the file doesn't exist or hasn't been touched recently enough to
    plausibly be describing what's running right now."""
    path = state_root / "auto-sync.log"
    if not path.is_file():
        return None
    if now - path.stat().st_mtime > _LOG_FRESH_WITHIN_SECONDS:
        return None
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-_LOG_TAIL_LINES:]
