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
that cost."""

from __future__ import annotations

import dataclasses

from fastapi import APIRouter, HTTPException, Request

from audiobook_manager.discover import discover_audiobooks
from audiobook_manager.pipeline import ImportPipelineError, run_import_audiobook

from common.config import ConfigError, load_global_config

from web_gui_backend.errors import config_error_response

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


@router.post("/api/audiobooks/discover/import")
def import_discovered_audiobook(body: dict, request: Request) -> dict:
    name = body.get("name", "")
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    root = _discover_root(request)
    if not root:
        raise HTTPException(
            status_code=422, detail="no discover_root configured for audiobook_manager"
        )

    parts_dir = f"{root.rstrip('/')}/{name}"
    library_root = request.app.state.library_root / "audiobooks"

    try:
        outcome = run_import_audiobook(
            parts_dir, library_root=library_root, state_root=request.app.state.state_root
        )
    except ImportPipelineError as e:
        # A real failure (ffmpeg/merge error, beets crashing) -- reaches
        # out to real external tools/network (Audible/Audnex lookups),
        # whose failure modes aren't enumerable up front, same "502, not
        # a raw 500" treatment every other external-call route here uses.
        raise HTTPException(status_code=502, detail=str(e)) from e

    if not outcome.imported:
        # Not a failure -- beets-audible just couldn't confidently match
        # this book. Surfaced as a normal (non-2xx) response so the
        # frontend can show "needs a metadata.yml, see the CLI docs"
        # rather than claiming success.
        raise HTTPException(
            status_code=422,
            detail="beets-audible could not confidently match this book -- "
            f"merged file left at {outcome.staging_dir}. Add a metadata.yml "
            "there and retry via `audiobook-manager tag` (see "
            "services/audiobook-manager/README.md).",
        )

    return {
        "status": "ok",
        "imported_paths": [str(p) for p in outcome.imported_paths],
    }
