from __future__ import annotations

import fcntl
import time
from pathlib import Path

_POLL_INTERVAL_SECONDS = 1.0


class LockTimeoutError(Exception):
    pass


class FileLock:
    """A cross-process exclusive lock backed by a file, using POSIX
    advisory locking (fcntl.flock — Linux/macOS only, this project's only
    deployment targets). Blocks (polling) until acquired or `timeout`
    seconds elapse, raising LockTimeoutError in the latter case, rather
    than either failing immediately or letting two holders proceed at
    once. Used to keep two `fetcher-apple fetch` runs (or any other
    single-session-limited operation) from overlapping."""

    def __init__(self, path: Path | str, timeout: float = 1800):
        self.path = Path(path)
        self.timeout = timeout
        self._fd = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = open(self.path, "a")
        deadline = time.monotonic() + self.timeout
        printed_waiting_message = False
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._fd.close()
                    self._fd = None
                    raise LockTimeoutError(
                        f"Timed out after {self.timeout}s waiting for lock at {self.path}"
                    )
                if not printed_waiting_message:
                    print(f"Waiting for lock at {self.path} (another session may be active)...")
                    printed_waiting_message = True
                # sleep only up to the deadline, not a full poll interval past
                # it — keeps short timeouts precise instead of overshooting.
                time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    def release(self) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None

    def __enter__(self) -> "FileLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
