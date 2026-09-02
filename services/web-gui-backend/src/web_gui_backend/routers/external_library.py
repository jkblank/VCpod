from __future__ import annotations

import dataclasses
from pathlib import Path

from fastapi import APIRouter, HTTPException

from web_gui_backend.browse import BrowseError, list_directory

router = APIRouter()


@router.get("/api/external-library/browse")
def browse_external_library(root: str, subpath: str = "") -> dict:
    # root is a real host path the user typed in (ExternalLibraryConfig.
    # path -- never /config/...-container-style, unlike credential
    # paths), not anything already resolved/trusted by this service --
    # see browse.py's own docstring for why this is the one route that
    # needs an explicit escape check.
    try:
        resolved_subpath, entries = list_directory(Path(root), subpath)
    except BrowseError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {
        "subpath": resolved_subpath,
        "entries": [dataclasses.asdict(e) for e in entries],
    }
