"""GET /api/alerts -- aggregates real, derived signals worth surfacing
on the Overview dashboard: missing/stale credential files and PO-token
companion-service reachability. Reuses the exact status logic already
exposed by sources.py/profile_sources.py/podcasts.py rather than
re-deriving it, so this can never drift from what the Sources/
Credentials/Podcasts screens themselves already show.

Every alert here is a fact this app can already observe (a file's real
mtime, a real TCP connect) -- never a guessed countdown like a
fictional "cookies expire in 9 days." See notes.md's "show something
real" principle.

Spotify is deliberately excluded -- shelved (Premium API requirement),
not actively used, so a missing credential for it is not real signal.
"""

from __future__ import annotations

import socket
import time
from urllib.parse import urlparse

from fastapi import APIRouter, Request

from common.config import ConfigError, load_all_profiles, load_global_config

from web_gui_backend.errors import config_error_response
from web_gui_backend.routers.podcasts import pocketcasts_status
from web_gui_backend.routers.profile_sources import profile_sources_status
from web_gui_backend.routers.sources import sources_status

router = APIRouter()

# Apple Music's cookies are the shortest-lived credential this app
# handles (gamdl's own docs: "expire every few weeks") -- used as the
# one shared staleness threshold across every credential type here
# rather than a separate guessed number per source.
_STALE_AFTER_DAYS = 14
_POT_PROVIDER_CONNECT_TIMEOUT = 1.5


def _file_alert(*, kind: str, profile: str | None, status: dict) -> dict | None:
    if not status["exists"]:
        return {"kind": kind, "profile": profile, "severity": "missing", "message": f"{kind} not saved yet"}
    age_days = (time.time() - status["updated_at"]) / 86400
    if age_days >= _STALE_AFTER_DAYS:
        return {
            "kind": kind,
            "profile": profile,
            "severity": "stale",
            "message": f"{kind} not updated in {int(age_days)}d",
        }
    return None


def _pot_provider_alert(pot_provider_url: str) -> dict | None:
    parsed = urlparse(pot_provider_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 4416
    try:
        with socket.create_connection((host, port), timeout=_POT_PROVIDER_CONNECT_TIMEOUT):
            return None
    except OSError:
        return {
            "kind": "PO-token provider",
            "profile": None,
            "severity": "unreachable",
            "message": f"PO-token provider not running ({host}:{port})",
        }


@router.get("/api/alerts")
def alerts(request: Request) -> dict:
    try:
        global_config = load_global_config(request.app.state.config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e

    status = sources_status(request)
    items: list[dict] = []

    if status["apple_music"]["enabled"]:
        alert = _file_alert(kind="Apple Music cookies", profile=None, status=status["apple_music"])
        if alert:
            items.append(alert)

    if status["ytmusic"]["enabled"]:
        alert = _file_alert(
            kind="YouTube Music cookies", profile=None, status=status["ytmusic"]["cookies"]
        )
        if alert:
            items.append(alert)
        pot_alert = _pot_provider_alert(global_config.sources.ytmusic.pot_provider_url)
        if pot_alert:
            items.append(pot_alert)

    profiles_dir = request.app.state.config_root / "profiles"
    profile_names = sorted(load_all_profiles(profiles_dir).keys()) if profiles_dir.is_dir() else []

    for name in profile_names:
        pc_status = pocketcasts_status(name, request)
        # Pocket Casts credentials have no household-wide default (unlike
        # apple_music/ytmusic) -- always per-profile, so unlike the
        # override-only checks below this always needs checking.
        alert = _file_alert(kind="Pocket Casts credentials", profile=name, status=pc_status)
        if alert:
            items.append(alert)

        profile_status = profile_sources_status(name, request)
        # apple_music/ytmusic already got a household-wide check above --
        # only check again here when the profile diverges with its own
        # override file, to avoid reporting the same missing/stale global
        # file once per profile that happens to share it.
        if status["apple_music"]["enabled"] and profile_status["apple_music"]["using"] == "override":
            alert = _file_alert(
                kind="Apple Music cookies (override)", profile=name, status=profile_status["apple_music"]
            )
            if alert:
                items.append(alert)
        if (
            status["ytmusic"]["enabled"]
            and profile_status["ytmusic"]["cookies"]["using"] == "override"
        ):
            alert = _file_alert(
                kind="YouTube Music cookies (override)",
                profile=name,
                status=profile_status["ytmusic"]["cookies"],
            )
            if alert:
                items.append(alert)

    return {"alerts": items}
