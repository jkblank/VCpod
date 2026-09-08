"""GET /api/overview -- assembles the Overview dashboard: one card per
profile (connected-device identity + real used/free bytes when
connected, else "not connected"; track/episode counts already believed
synced; the last real sync time; the next scheduled fetch), this same
process's alerts.py aggregation, whole-library stats, and recent
activity. Every field here is either already-plumbed state (StateDB
counts, common.activity's last-sync marker, common.schedule's
next-fetch computation) or a live read (identify_connected_devices, a
library/music file count) -- nothing fabricated, same "show something
real" principle as alerts.py.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request

from common.activity import get_last_sync, list_activity
from common.config import ConfigError, load_all_profiles, load_global_config
from common.models import ProfileConfig
from common.schedule import next_profile_fetch_time
from common.state import StateDB

from web_gui_backend.device import DeviceIdentifyError, identify_connected_devices
from web_gui_backend.errors import config_error_response
from web_gui_backend.routers.alerts import alerts as _alerts_route

router = APIRouter()


def _match_connected_device(profile: ProfileConfig, connected_devices: list[dict]) -> dict | None:
    for device in connected_devices:
        field = "volume_label" if profile.device.match_by == "volume_label" else "serial"
        if device.get(field) == profile.device.match_value:
            return device
    return None


def _library_track_count(library_root: Path) -> int:
    music_root = library_root / "music"
    if not music_root.is_dir():
        return 0
    # rglob over the real library -- acceptable for a personal-library
    # size; flagged in the plan as a possible perf follow-up if it ever
    # isn't.
    return sum(1 for p in music_root.rglob("*") if p.is_file())


def _device_card(
    *, name: str, profile: ProfileConfig, connected_devices: list[dict], state_root: Path, now: datetime
) -> dict:
    connected = _match_connected_device(profile, connected_devices)
    with StateDB(state_root / f"{name}.sqlite") as db:
        track_count = db.count_tracks()
        episode_count = db.count_episodes()
        unplayed_episode_count = db.count_episodes(unplayed_only=True)
        next_fetch = next_profile_fetch_time(profile, db, now)

    last_sync = get_last_sync(state_root, name)
    return {
        "profile": name,
        "connected_device": connected,
        "track_count": track_count,
        "episode_count": episode_count,
        "unplayed_episode_count": unplayed_episode_count,
        "last_sync": last_sync.isoformat() if last_sync else None,
        "next_fetch": next_fetch.isoformat() if next_fetch else None,
    }


@router.get("/api/overview")
def overview(request: Request) -> dict:
    config_root = request.app.state.config_root
    library_root = request.app.state.library_root
    state_root = request.app.state.state_root

    try:
        load_global_config(config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e

    profiles_dir = config_root / "profiles"
    profiles = load_all_profiles(profiles_dir) if profiles_dir.is_dir() else {}

    try:
        connected_devices = identify_connected_devices(request.app.state.sync_orchestrator_dir)
    except DeviceIdentifyError:
        # A real failure to even ask (e.g. sync-orchestrator isn't
        # runnable in this environment) degrades to "nothing connected"
        # for the dashboard rather than failing the whole route -- every
        # other card here is still real and useful without it.
        connected_devices = []

    now = datetime.now(timezone.utc)
    device_cards = [
        _device_card(
            name=name, profile=profile, connected_devices=connected_devices,
            state_root=state_root, now=now,
        )
        for name, profile in sorted(profiles.items())
    ]

    recent_activity = [
        {
            "started_at": entry.started_at.isoformat(),
            "service": entry.service,
            "profile": entry.profile,
            "description": entry.description,
            "duration_seconds": entry.duration_seconds,
            "result": entry.result,
        }
        for entry in list_activity(state_root, limit=5)
    ]

    return {
        "devices": device_cards,
        "alerts": _alerts_route(request)["alerts"],
        "library": {"track_count": _library_track_count(library_root)},
        "recent_activity": recent_activity,
    }
