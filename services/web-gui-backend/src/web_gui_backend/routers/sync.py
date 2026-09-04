"""Compute/execute a real device sync from the browser -- streams
`sync-orchestrator sync --json`'s progress + result over SSE (see
sync_runner.py). Two routes, deliberately independent of each other
(confirmed with the user): /plan never executes anything, /execute
never requires a prior /plan call -- same trust model the CLI already
has (`sync --execute --allow-removals` can already be run directly,
with no forced plan-only run first). sync-orchestrator's own hard gate
(refuses --execute without --allow-removals whenever there's anything
to remove) is what actually backstops this, not any request
sequencing."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from common.config import ConfigError, resolve_profile_path

from web_gui_backend.device import _default_sync_orchestrator_dir
from web_gui_backend.sync_runner import stream_sync
from web_gui_backend.sync_status import is_sync_running, recent_auto_sync_log_tail

router = APIRouter()


def _sse(event: str, data: str) -> str:
    # Multi-line-safe SSE framing: one "data: " line per line of data,
    # per the SSE spec (a browser EventSource/manual parser concatenates
    # consecutive data: lines with '\n' when reconstructing). Every event
    # this route emits happens to be single-line today (a stderr log
    # line, or sync-orchestrator's own single-line json.dumps(...)
    # output) but this stays correct if that ever changes.
    lines = data.splitlines() or [""]
    data_block = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{data_block}\n\n"


async def _sync_events(*, request: Request, body: dict, execute: bool) -> AsyncIterator[str]:
    profile_name = body.get("profile", "")
    if not profile_name:
        yield _sse("error", "profile is required")
        return

    config_root = request.app.state.config_root
    try:
        profile_path = resolve_profile_path(profile_name, config_root)
    except ConfigError as e:
        yield _sse("error", str(e))
        return

    sync_orchestrator_dir = (
        request.app.state.sync_orchestrator_dir or _default_sync_orchestrator_dir()
    )

    args = [
        "--profile", str(profile_path),
        "--library-root", str(request.app.state.library_root),
        "--state-root", str(request.app.state.state_root),
    ]
    if body.get("skip_backup"):
        args.append("--skip-backup")
    if body.get("skip_podcasts"):
        args.append("--skip-podcasts")
    if execute:
        args.append("--execute")
        if body.get("allow_removals"):
            args.append("--allow-removals")

    async for event, data in stream_sync(args=args, sync_orchestrator_dir=sync_orchestrator_dir):
        yield _sse(event, data)


@router.post("/api/sync/plan")
async def compute_sync_plan(body: dict, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _sync_events(request=request, body=body, execute=False),
        media_type="text/event-stream",
    )


@router.post("/api/sync/execute")
async def execute_sync_now(body: dict, request: Request) -> StreamingResponse:
    return StreamingResponse(
        _sync_events(request=request, body=body, execute=True),
        media_type="text/event-stream",
    )


@router.get("/api/sync/status")
def sync_status(profile: str, request: Request) -> dict:
    """Is a sync running for this profile right now, regardless of who
    started it -- a fresh page load, a different browser/device, or a
    headless auto-sync run (which never goes through this backend's own
    /api/sync/execute at all, see sync_status.py). Read-only, cannot
    interfere with a real sync in progress -- see sync_status.py's
    docstring for why."""
    state_root = request.app.state.state_root
    running = is_sync_running(state_root, profile)
    log_tail = recent_auto_sync_log_tail(state_root, now=time.time()) if running else None
    return {"running": running, "log_tail": log_tail}
