"""POST /api/profiles/{name}/device-decision -- records the first-sync choice
for one iPod ("remove" its untracked tracks, or "adopt" them into the
profile's index), via `sync-orchestrator device-decision` (a subprocess, same
reason eject.py gives). Refuses while a sync runs for that profile."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from web_gui_backend.device import _default_sync_orchestrator_dir
from web_gui_backend.sync_status import is_sync_running

router = APIRouter()


class DeviceDecisionBody(BaseModel):
    serial: str = Field(min_length=1)
    decision: Literal["remove", "adopt"]


@router.post("/api/profiles/{name}/device-decision")
def record_device_decision(name: str, body: DeviceDecisionBody, request: Request) -> dict:
    profile_path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    if not profile_path.is_file():
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}")
    if is_sync_running(request.app.state.state_root, name):
        raise HTTPException(
            status_code=409,
            detail=f"a sync is running for {name!r}; record the decision once it finishes",
        )

    project_dir = request.app.state.sync_orchestrator_dir or _default_sync_orchestrator_dir()
    result = subprocess.run(
        [
            "uv", "run", "--project", str(Path(project_dir)),
            "sync-orchestrator", "device-decision",
            "--profile", str(profile_path),
            "--state-root", str(request.app.state.state_root),
            "--serial", body.serial,
            "--decision", body.decision,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=409, detail=(result.stdout or result.stderr).strip())
    return {"recorded": True, "decision": body.decision, "serial": body.serial}
