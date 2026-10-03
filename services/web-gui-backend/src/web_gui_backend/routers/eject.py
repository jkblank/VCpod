"""POST /api/profiles/{name}/eject -- ejects the iPod matched by a profile,
via `sync-orchestrator eject` (a subprocess, same reason device.py gives).
Refuses while a sync is running for that profile: ejecting mid-sync is
exactly the unclean-unmount corruption this exists to prevent."""

from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from web_gui_backend.device import _default_sync_orchestrator_dir
from web_gui_backend.sync_status import is_sync_running

router = APIRouter()


@router.post("/api/profiles/{name}/eject")
def eject_profile_device(name: str, request: Request) -> dict:
    profile_path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    if not profile_path.is_file():
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}")
    if is_sync_running(request.app.state.state_root, name):
        raise HTTPException(
            status_code=409,
            detail=f"a sync is running for {name!r}; eject once it finishes",
        )

    project_dir = request.app.state.sync_orchestrator_dir or _default_sync_orchestrator_dir()
    result = subprocess.run(
        [
            "uv", "run", "--project", str(Path(project_dir)),
            "sync-orchestrator", "eject", "--profile", str(profile_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    message = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise HTTPException(status_code=409, detail=message or "eject failed")
    return {"ejected": True, "message": message}
