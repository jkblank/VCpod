import fcntl
import time
from pathlib import Path

from web_gui_backend.sync_status import is_sync_running, recent_auto_sync_log_tail


def test_is_sync_running_false_when_no_lock_file_exists(tmp_path: Path):
    assert is_sync_running(tmp_path, "john") is False


def test_is_sync_running_false_no_side_effect_when_no_lock_file(tmp_path: Path):
    is_sync_running(tmp_path, "john")

    assert not (tmp_path / ".sync_john.lock").exists()


def test_is_sync_running_false_when_lock_file_exists_but_unheld(tmp_path: Path):
    # A lock file persists (empty) after a past sync released it --
    # only an actively-held flock means "running", not mere existence.
    (tmp_path / ".sync_john.lock").touch()

    assert is_sync_running(tmp_path, "john") is False


def test_is_sync_running_true_when_another_process_holds_the_lock(tmp_path: Path):
    lock_path = tmp_path / ".sync_john.lock"
    holder_fd = open(lock_path, "a")
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert is_sync_running(tmp_path, "john") is True
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        holder_fd.close()


def test_is_sync_running_probe_releases_immediately_does_not_block_real_holder(tmp_path: Path):
    # The probe must never itself end up holding the lock -- confirm a
    # second real acquire attempt right after a "not running" probe
    # still succeeds immediately (would hang/raise if the probe leaked
    # its own lock).
    assert is_sync_running(tmp_path, "john") is False

    lock_path = tmp_path / ".sync_john.lock"
    fd = open(lock_path, "a")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # would raise if still held
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_is_sync_running_scoped_per_profile(tmp_path: Path):
    lock_path = tmp_path / ".sync_alice.lock"
    holder_fd = open(lock_path, "a")
    fcntl.flock(holder_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert is_sync_running(tmp_path, "bob") is False
    finally:
        fcntl.flock(holder_fd, fcntl.LOCK_UN)
        holder_fd.close()


def test_recent_auto_sync_log_tail_none_when_missing(tmp_path: Path):
    assert recent_auto_sync_log_tail(tmp_path, now=time.time()) is None


def test_recent_auto_sync_log_tail_returns_last_lines(tmp_path: Path):
    log = tmp_path / "auto-sync.log"
    log.write_text("\n".join(f"line {i}" for i in range(30)) + "\n")

    tail = recent_auto_sync_log_tail(tmp_path, now=time.time())

    assert tail is not None
    assert len(tail) == 20
    assert tail[-1] == "line 29"


def test_recent_auto_sync_log_tail_none_when_stale(tmp_path: Path):
    import os

    log = tmp_path / "auto-sync.log"
    log.write_text("old run\n")
    old_time = time.time() - (3 * 60 * 60)
    os.utime(log, (old_time, old_time))

    assert recent_auto_sync_log_tail(tmp_path, now=time.time()) is None
