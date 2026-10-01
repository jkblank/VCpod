"""Bridges audiobook_manager.pipeline.run_import_audiobook -- a blocking,
in-process, callback-based function (ffmpeg + `beet import` subprocesses
underneath, no asyncio anywhere) -- into an async generator of
(event, data) tuples, same shape as sync_runner.stream_sync, for
streaming over SSE from routers/audiobooks_discover.py.

Unlike stream_sync (a genuinely separate subprocess whose own stdout/
stderr pipes are naturally async-iterable), audiobook-manager is
imported in-process here (see routers/audiobooks_discover.py's own
docstring for why) -- there's no subprocess stdio to read. Progress
instead arrives via run_import_audiobook's own progress_callback,
invoked synchronously from a background thread (loop.run_in_executor);
each call is relayed onto an asyncio.Queue via call_soon_threadsafe
(the standard way to get a callback running on a worker thread to wake
up a coroutine waiting on the event loop) so this generator can yield
it without ever running pipeline code on the event loop thread itself,
which would block every other request this backend is serving for
however long a real merge+tag takes (minutes, for a long book)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

from audiobook_manager.pipeline import ImportPipelineError, run_import_audiobook


async def stream_import_audiobook(
    parts_dir: Path | str, *, library_root: Path | str, state_root: Path | str
) -> AsyncIterator[tuple[str, str]]:
    """Runs run_import_audiobook in a background thread, yielding events
    as it runs:

    - ("progress", message) for each progress_callback call, as it
      happens.
    - ("result", json_text) once it finishes without raising -- a JSON
      object with imported/imported_paths/staging_dir, the same shape
      the route returned directly before this became a stream.
    - ("error", message) if it raises ImportPipelineError.

    Never raises -- same contract as stream_sync, so a caller streaming
    this straight into an HTTP response never has to catch anything
    mid-stream."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue()

    def progress_callback(message: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, ("progress", message))

    def _run() -> None:
        try:
            outcome = run_import_audiobook(
                parts_dir,
                library_root=library_root,
                state_root=state_root,
                progress_callback=progress_callback,
            )
        except ImportPipelineError as e:
            loop.call_soon_threadsafe(queue.put_nowait, ("error", str(e)))
        else:
            payload = json.dumps(
                {
                    "imported": outcome.imported,
                    "imported_paths": [str(p) for p in outcome.imported_paths],
                    "staging_dir": str(outcome.staging_dir),
                }
            )
            loop.call_soon_threadsafe(queue.put_nowait, ("result", payload))
        finally:
            # Sentinel: guarantees the while loop below terminates even
            # if _run's own try/except/else above never puts anything
            # (it always does, but this stays correct if that changes).
            loop.call_soon_threadsafe(queue.put_nowait, None)

    future = loop.run_in_executor(None, _run)
    while True:
        item = await queue.get()
        if item is None:
            break
        yield item
    await future
