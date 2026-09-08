"""GET /api/homepage/status -- one flat JSON summary built for a
gethomepage.dev customapi widget tile: homepage's customapi mapping
reads flat top-level fields, not a nested per-profile dashboard, so
this deliberately doesn't reuse /api/overview's device-card shape --
same "purpose-built aggregation" pattern alerts.py/overview.py already
each are, just flattened one level further for this one consumer.

Every field here is the same real, already-plumbed state overview.py/
alerts.py/sync_status.py compute -- no new signal invented for this
endpoint, same "show something real" principle as the rest of this app.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from common.activity import list_activity
from common.config import ConfigError, load_all_profiles, load_global_config

from web_gui_backend.device import DeviceIdentifyError, identify_connected_devices
from web_gui_backend.errors import config_error_response
from web_gui_backend.routers.alerts import alerts as _alerts_route
from web_gui_backend.sync_status import is_sync_running

router = APIRouter()

# list_activity has no per-service filter of its own (nothing else has
# needed one yet) -- this looks back far enough into the small page it
# already returns to find the most recent sync-orchestrator entry even
# on a household where fetch-scheduler entries interleave with it.
_ACTIVITY_LOOKBACK = 25


@router.get("/api/homepage/status")
def homepage_status(request: Request) -> dict:
    config_root = request.app.state.config_root
    state_root = request.app.state.state_root

    try:
        load_global_config(config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e

    profiles_dir = config_root / "profiles"
    profile_names = sorted(load_all_profiles(profiles_dir).keys()) if profiles_dir.is_dir() else []

    sync_running = any(is_sync_running(state_root, name) for name in profile_names)

    last_sync = next(
        (
            entry
            for entry in list_activity(state_root, limit=_ACTIVITY_LOOKBACK)
            if entry.service == "sync-orchestrator"
        ),
        None,
    )

    try:
        connected_devices = identify_connected_devices(request.app.state.sync_orchestrator_dir)
    except DeviceIdentifyError:
        # Same degrade-to-empty treatment overview.py gives this --
        # a real failure to even ask shouldn't fail the whole tile.
        connected_devices = []

    alerts_count = len(_alerts_route(request)["alerts"])

    if sync_running:
        status = "syncing"
    elif last_sync is not None and last_sync.result == "error":
        status = "error"
    elif alerts_count:
        status = "attention"
    else:
        status = "ok"

    return {
        "status": status,
        "sync_running": sync_running,
        "profiles_count": len(profile_names),
        "devices_connected": len(connected_devices),
        "alerts_count": alerts_count,
        "last_sync_at": last_sync.started_at.isoformat() if last_sync else None,
        "last_sync_profile": last_sync.profile if last_sync else None,
        "last_sync_result": last_sync.result if last_sync else None,
        "last_sync_description": last_sync.description if last_sync else None,
    }
