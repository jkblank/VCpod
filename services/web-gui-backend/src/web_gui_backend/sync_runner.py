"""Streams a `sync-orchestrator sync --json` subprocess to a caller as it
runs -- the first streaming-subprocess building block in this service
(everything else so far either imports in-process, like fetcher_apple/
podcast_manager, or uses subprocess.run's buffer-until-exit, like
device.py's identify_connected_devices). sync-orchestrator is a
standalone `uv` project (isolated venv for its iopenpod/PyQt6 deps,
same reasoning device.py's own docstring already gives), so this stays
a subprocess call, never an in-process import.

Progress lines land on the subprocess's stderr and result/plan JSON
lands on stdout -- see sync_orchestrator/cli.py's own --json handling,
which this is the other half of."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path


async def stream_sync(
    *, args: list[str], sync_orchestrator_dir: Path | str
) -> AsyncIterator[tuple[str, str]]:
    """Runs `uv run --project <sync_orchestrator_dir> sync-orchestrator
    sync --json <args...>`, yielding events as the process runs:

    - ("progress", line) for each stderr line, as it arrives.
    - ("result", json_text) once the process exits 0 and stdout's
      collected content parses as JSON (the plan or the executed
      result, per sync_orchestrator/plan_json.py).
    - ("error", message) if the subprocess can't even start, exits
      non-zero, or its stdout doesn't parse as JSON -- covers both a
      real sync failure (cli.py's _fail already put a plain message on
      stdout) and a genuinely unexpected crash.

    Never raises -- every failure mode is reported as an ("error", ...)
    event so a caller streaming this straight into an HTTP response
    never has to catch anything mid-stream."""
    argv = [
        "uv", "run", "--project", str(sync_orchestrator_dir),
        "sync-orchestrator", "sync", "--json", *args,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        yield ("error", f"could not start sync-orchestrator: {e}")
        return

    assert proc.stdout is not None and proc.stderr is not None

    stdout_lines: list[str] = []

    async def _drain_stdout() -> None:
        async for raw in proc.stdout:
            stdout_lines.append(raw.decode(errors="replace").rstrip("\n"))

    # stdout only ever carries 0-1 lines under --json (see cli.py) --
    # no need to stream it live, just drain it in the background while
    # stderr's progress lines are yielded as they arrive below.
    stdout_task = asyncio.create_task(_drain_stdout())

    async for raw in proc.stderr:
        yield ("progress", raw.decode(errors="replace").rstrip("\n"))

    await stdout_task
    returncode = await proc.wait()
    stdout_text = "\n".join(stdout_lines).strip()

    if returncode != 0:
        yield ("error", stdout_text or f"sync-orchestrator exited {returncode}")
        return
    try:
        json.loads(stdout_text)
    except (json.JSONDecodeError, ValueError):
        yield ("error", f"could not parse sync-orchestrator output: {stdout_text!r}")
        return
    yield ("result", stdout_text)
