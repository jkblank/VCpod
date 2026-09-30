"""Discover raw, not-yet-processed audiobook source folders (see
audiobook-manager's own README for the manual Libby-capture workflow
this feeds) and optionally kick off the merge+tag pipeline against one
of them, without leaving the browser.

audiobook-manager is a root-workspace member (unlike sync-orchestrator/
fetcher-spotify) so importing its `discover`/`pipeline` modules
in-process here doesn't introduce a new isolated dependency tree --
beets-audible is already installed in this exact venv. discover.py
itself never imports beets (see its own docstring); only the
POST .../import route below, which actually runs the pipeline, pays
that cost.

That POST route streams progress over SSE (audiobook_runner.py) rather
than blocking until the whole merge+tag pipeline finishes -- a real
multi-hour audiobook's ffmpeg concat/encode plus beets-audible's Audible
lookup can take minutes, and the route used to just hang with zero
feedback until then. Same event shape (progress/result/error) as
routers/sync.py's SSE routes, for the same reason."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from audiobook_manager.discover import discover_audiobooks

from common.config import ConfigError, load_global_config

from web_gui_backend.audiobook_runner import stream_import_audiobook
from web_gui_backend.errors import config_error_response
from web_gui_backend.sse import sse_event

router = APIRouter()


def _discover_root(request: Request) -> str:
    try:
        config = load_global_config(request.app.state.config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e
    return config.audiobook_manager.discover_root


@router.get("/api/audiobooks/discover")
def list_discovered_audiobooks(request: Request) -> dict:
    root = _discover_root(request)
    if not root:
        return {"root": "", "books": []}
    books = discover_audiobooks(root, request.app.state.state_root)
    return {"root": root, "books": [dataclasses.asdict(b) for b in books]}


async def _import_events(*, request: Request, name: str, root: str) -> AsyncIterator[str]:
    if not name:
        yield sse_event("error", "name is required")
        return
    if not root:
        yield sse_event("error", "no discover_root configured for audiobook_manager")
        return

    parts_dir = f"{root.rstrip('/')}/{name}"
    library_root = request.app.state.library_root / "audiobooks"

    async for event, data in stream_import_audiobook(
        parts_dir, library_root=library_root, state_root=request.app.state.state_root
    ):
        if event == "result":
            payload = json.loads(data)
            if not payload["imported"]:
                # Not a failure -- beets-audible just couldn't confidently
                # match this book. Still an "error" event (there's no
                # separate SSE status-code channel to distinguish this
                # from a real failure), same as every other error case
                # below/above -- the frontend already treats all of them
                # uniformly (see AudiobookDiscovery.tsx's importError
                # state), so this loses no behavior the non-streaming
                # 422-vs-502 split actually provided.
                yield sse_event(
                    "error",
                    "beets-audible could not confidently match this book -- merged file "
                    f"left at {payload['staging_dir']}. Add a metadata.yml there and retry "
                    "via `audiobook-manager tag` (see services/audiobook-manager/README.md).",
                )
                return
        yield sse_event(event, data)


@router.post("/api/audiobooks/discover/import")
def import_discovered_audiobook(body: dict, request: Request) -> StreamingResponse:
    name = body.get("name", "")
    root = _discover_root(request)
    return StreamingResponse(
        _import_events(request=request, name=name, root=root),
        media_type="text/event-stream",
    )
