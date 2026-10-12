"""POST /api/profiles/{name}/fetch-now -- runs fetch-scheduler's own
per-profile fetch immediately, ignoring each target's schedule. Blocking:
a real fetch can take minutes (downloads), and the browser simply waits
on this one request; the button shows a busy state for that duration.
Same lock as the scheduler (fetch-scheduler's FileLock on the profile),
so this never overlaps a scheduled fetch of the same profile."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from common.config import ConfigError, load_all_profiles
from common.lock import LockTimeoutError

from fetch_scheduler.loop import fetch_profile_now

router = APIRouter()


@router.post("/api/profiles/{name}/fetch-now")
def fetch_profile(name: str, request: Request) -> dict:
    config_root = request.app.state.config_root
    try:
        profiles = load_all_profiles(config_root / "profiles")
    except ConfigError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if name not in profiles:
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}")

    try:
        result = fetch_profile_now(
            profile_name=name,
            config_root=config_root,
            library_root=request.app.state.library_root,
            state_root=request.app.state.state_root,
            now=datetime.now(timezone.utc),
        )
    except LockTimeoutError as e:
        raise HTTPException(
            status_code=409, detail=f"another fetch for {name!r} is already running: {e}"
        ) from e

    return {
        "fetched": result.fetched.get(name, []),
        "source_errors": result.source_errors.get(name, []),
        "errors": [name] if name in result.errors else [],
    }
