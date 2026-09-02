"""Per-profile Pocket Casts subscription browsing + credential capture.

Unlike Apple Music/YouTube Music (one shared household login in
global.yaml), Pocket Casts credentials are per-profile
(config/secrets/pocketcasts/<profile>.json) -- matches
ProfilePocketCastsConfig.credentials_file already being a per-profile
field, not a global.yaml one."""

from __future__ import annotations

import dataclasses
import json

from fastapi import APIRouter, HTTPException, Request

from common.config import ConfigError, load_profile_config, resolve_config_path

from podcast_manager.api import list_subscriptions, load_credentials, login

from web_gui_backend.atomic import write_text_atomic

router = APIRouter()


def _profile(request: Request, name: str):
    path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    try:
        return load_profile_config(path)
    except ConfigError as e:
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}") from e


@router.get("/api/profiles/{name}/pocketcasts/subscriptions")
def pocketcasts_subscriptions(name: str, request: Request) -> list[dict]:
    profile = _profile(request, name)
    creds_path = resolve_config_path(
        profile.podcasts.pocketcasts.credentials_file, request.app.state.config_root
    )
    if not creds_path.is_file():
        raise HTTPException(
            status_code=502,
            detail="Pocket Casts credentials not saved yet for this profile",
        )
    try:
        email, password = load_credentials(creds_path)
        token = login(email, password)
        subscriptions = list_subscriptions(token)
    except Exception as e:
        # Broad on purpose -- a saved-but-now-stale password, a Pocket
        # Casts API hiccup, a malformed credentials file are all real,
        # non-enumerable failure modes at this boundary; see sources.py's
        # matching comment on its own list routes.
        raise HTTPException(
            status_code=502, detail=f"could not list Pocket Casts subscriptions: {e}"
        ) from e
    return [dataclasses.asdict(s) for s in subscriptions]


@router.put("/api/profiles/{name}/pocketcasts-credentials")
def put_pocketcasts_credentials(name: str, body: dict, request: Request) -> dict:
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password are both required")

    # Cheap, local check first -- no point making a real network call to
    # Pocket Casts for a profile name that doesn't even exist.
    profile = _profile(request, name)

    try:
        # Validates by actually logging in *before* writing anything --
        # a bad credential pair never gets saved, unlike a plain "does
        # this parse as JSON" check.
        login(email, password)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail=f"Pocket Casts rejected these credentials: {e}"
        ) from e

    creds_path = resolve_config_path(
        profile.podcasts.pocketcasts.credentials_file, request.app.state.config_root
    )
    write_text_atomic(json.dumps({"email": email, "password": password}), creds_path)
    return {"status": "ok"}
