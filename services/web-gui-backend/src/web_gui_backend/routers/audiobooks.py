from __future__ import annotations

import dataclasses

from fastapi import APIRouter, HTTPException, Request

from web_gui_backend.browse import BrowseError, list_directory

router = APIRouter()


@router.get("/api/audiobooks/browse")
def browse_audiobooks(request: Request, subpath: str = "") -> dict:
    # Unlike external-library, the root here is always library_root/
    # audiobooks -- a real, managed path this service already knows
    # (app.state.library_root), not something the client passes in.
    root = request.app.state.library_root / "audiobooks"
    try:
        resolved_subpath, entries = list_directory(root, subpath)
    except BrowseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "subpath": resolved_subpath,
        "entries": [dataclasses.asdict(e) for e in entries],
    }
