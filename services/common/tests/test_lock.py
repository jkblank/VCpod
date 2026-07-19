import threading
import time
from pathlib import Path

import pytest

from common.lock import FileLock, LockTimeoutError


def test_acquire_and_release_round_trip(tmp_path: Path):
    lock_path = tmp_path / "test.lock"
    with FileLock(lock_path, timeout=2):
        pass
    # released cleanly on exit — immediately re-acquirable
    with FileLock(lock_path, timeout=2):
        pass


def test_second_lock_waits_for_first_to_release(tmp_path: Path):
    lock_path = tmp_path / "test.lock"
    released_at = {}
    acquired_at = {}

    def hold_lock():
        with FileLock(lock_path, timeout=5):
            time.sleep(0.3)
            released_at["time"] = time.monotonic()

    holder = threading.Thread(target=hold_lock)
    holder.start()
    time.sleep(0.05)  # let the holder acquire first

    start = time.monotonic()
    with FileLock(lock_path, timeout=5):
        acquired_at["time"] = time.monotonic()

    holder.join()
    assert acquired_at["time"] >= released_at["time"]
    assert acquired_at["time"] - start >= 0.2  # genuinely waited, not instant


def test_lock_timeout_raises_when_held_too_long(tmp_path: Path):
    lock_path = tmp_path / "test.lock"

    def hold_lock_long():
        with FileLock(lock_path, timeout=5):
            time.sleep(1.0)

    holder = threading.Thread(target=hold_lock_long)
    holder.start()
    time.sleep(0.05)

    start = time.monotonic()
    with pytest.raises(LockTimeoutError):
        with FileLock(lock_path, timeout=0.2):
            pass
    elapsed = time.monotonic() - start
    assert elapsed < 0.6  # didn't overshoot into a full extra poll interval

    holder.join()
