from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from web_gui_backend.device import DeviceIdentifyError, identify_connected_devices

router = APIRouter()


@router.get("/api/device/identify")
def identify_device(request: Request) -> dict:
    try:
        devices = identify_connected_devices(request.app.state.sync_orchestrator_dir)
    except DeviceIdentifyError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"devices": devices}
