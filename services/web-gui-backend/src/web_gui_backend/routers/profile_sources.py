"""Per-profile Apple Music/YouTube Music credential overrides.

global.yaml's `sources:` block (see routers/sources.py) is the
household's shared default -- every profile used it, with no way to
diverge, until this router existed. A profile only gets its own
`sources:` override via one of the explicit actions below (set up
separate credentials, or "import" -- point at the exact same file
another profile already references, never a byte copy) -- confirmed
with the user after a real report: a newly-created profile showed up
already "connected" to Apple Music, since there was only ever one
shared credential file to begin with. See common.models.
ProfileSourcesConfig's docstring and notes.md.

Spotify is deliberately not exposed here -- it has no capture routes
in sources.py either (shelved, blocked on a Premium API requirement
outside this project's control), so there's no override UI to offer
yet even though the schema (ProfileSourcesConfig.spotify) already
supports one for later/manual use.
"""

from __future__ import annotations

import dataclasses
import json

from fastapi import APIRouter, HTTPException, Request

from common.config import (
    ConfigError,
    load_global_config,
    load_profile_config,
    resolve_apple_music_cookies,
    resolve_config_path,
    resolve_ytmusic_cookies,
    resolve_ytmusic_oauth,
    resolve_ytmusic_oauth_client,
    save_profile_config,
)
from common.models import (
    ProfileAppleMusicOverride,
    ProfileConfig,
    ProfileSourcesConfig,
    ProfileYtMusicOverride,
)

from fetcher_apple.api import list_playlists as list_apple_music_playlists
from fetcher_ytmusic.api import list_playlists as list_ytmusic_playlists
from fetcher_ytmusic.oauth import (
    OAuthFlowError,
    OAuthPending,
    poll_device_flow,
    start_device_flow,
)

from web_gui_backend.atomic import write_text_atomic
from web_gui_backend.errors import config_error_response
from web_gui_backend.routers.sources import _validate_cookies_txt

router = APIRouter()


def _profile_path(request: Request, name: str):
    return request.app.state.config_root / "profiles" / f"{name}.yaml"


def _profile(request: Request, name: str) -> ProfileConfig:
    try:
        return load_profile_config(_profile_path(request, name))
    except ConfigError as e:
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}") from e


def _global_config(request: Request):
    try:
        return load_global_config(request.app.state.config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e


def _file_status(path) -> dict:
    if not path.is_file():
        return {"exists": False, "updated_at": None}
    return {"exists": True, "updated_at": path.stat().st_mtime}


def _effective_apple_music_container_path(profile: ProfileConfig, global_config) -> str:
    if profile.sources and profile.sources.apple_music:
        return profile.sources.apple_music.cookies_file
    return global_config.sources.apple_music.cookies_file


def _effective_ytmusic_cookies_container_path(profile: ProfileConfig, global_config) -> str:
    override = profile.sources.ytmusic if profile.sources else None
    if override and override.cookies_file:
        return override.cookies_file
    return global_config.sources.ytmusic.cookies_file


def _save_with_apple_music_override(request: Request, profile: ProfileConfig, container_path: str) -> None:
    existing = profile.sources or ProfileSourcesConfig()
    updated_sources = existing.model_copy(
        update={"apple_music": ProfileAppleMusicOverride(cookies_file=container_path)}
    )
    updated_profile = profile.model_copy(update={"sources": updated_sources})
    save_profile_config(updated_profile, _profile_path(request, profile.profile))


def _save_with_ytmusic_override(request: Request, profile: ProfileConfig, **fields) -> None:
    existing_sources = profile.sources or ProfileSourcesConfig()
    existing_yt = existing_sources.ytmusic or ProfileYtMusicOverride()
    updated_yt = existing_yt.model_copy(update=fields)
    updated_sources = existing_sources.model_copy(update={"ytmusic": updated_yt})
    updated_profile = profile.model_copy(update={"sources": updated_sources})
    save_profile_config(updated_profile, _profile_path(request, profile.profile))


@router.get("/api/profiles/{name}/sources/status")
def profile_sources_status(name: str, request: Request) -> dict:
    profile = _profile(request, name)
    global_config = _global_config(request)
    config_root = request.app.state.config_root

    apple_override = bool(profile.sources and profile.sources.apple_music)
    yt_override = profile.sources.ytmusic if profile.sources else None

    return {
        "apple_music": {
            "using": "override" if apple_override else "global",
            **_file_status(resolve_apple_music_cookies(profile, global_config, config_root)),
        },
        "ytmusic": {
            "cookies": {
                "using": "override" if (yt_override and yt_override.cookies_file) else "global",
                **_file_status(resolve_ytmusic_cookies(profile, global_config, config_root)),
            },
            "oauth": {
                "using": "override" if (yt_override and yt_override.oauth_file) else "global",
                **_file_status(resolve_ytmusic_oauth(profile, global_config, config_root)),
            },
            "oauth_client": {
                "using": "override" if (yt_override and yt_override.oauth_client_file) else "global",
                **_file_status(resolve_ytmusic_oauth_client(profile, global_config, config_root)),
            },
        },
    }


@router.get("/api/profiles/{name}/sources/apple-music/playlists")
def profile_apple_music_playlists(name: str, request: Request) -> list[dict]:
    profile = _profile(request, name)
    global_config = _global_config(request)
    cookies_path = resolve_apple_music_cookies(profile, global_config, request.app.state.config_root)
    try:
        playlists = list_apple_music_playlists(str(cookies_path))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"could not list Apple Music playlists: {e}"
        ) from e
    return [dataclasses.asdict(p) for p in playlists]


@router.get("/api/profiles/{name}/sources/ytmusic/playlists")
def profile_ytmusic_playlists(name: str, request: Request) -> list[dict]:
    profile = _profile(request, name)
    global_config = _global_config(request)
    config_root = request.app.state.config_root
    oauth_path = resolve_ytmusic_oauth(profile, global_config, config_root)
    client_id = client_secret = None
    client_path = resolve_ytmusic_oauth_client(profile, global_config, config_root)
    if client_path.is_file():
        try:
            data = json.loads(client_path.read_text(encoding="utf-8"))
            client_id, client_secret = data["client_id"], data["client_secret"]
        except (json.JSONDecodeError, KeyError):
            pass
    try:
        playlists = list_ytmusic_playlists(
            str(oauth_path), oauth_client_id=client_id, oauth_client_secret=client_secret
        )
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"could not list YouTube Music playlists: {e}"
        ) from e
    return [dataclasses.asdict(p) for p in playlists]


@router.put("/api/profiles/{name}/sources/apple-music/cookies")
def put_profile_apple_music_cookies(name: str, body: dict, request: Request) -> dict:
    content = body.get("cookies_txt", "")
    try:
        _validate_cookies_txt(content, require_apple_token=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    profile = _profile(request, name)
    container_path = f"/config/secrets/{name}/apple_music_cookies.txt"
    target = resolve_config_path(container_path, request.app.state.config_root)
    write_text_atomic(content, target)
    _save_with_apple_music_override(request, profile, container_path)
    return {"status": "ok"}


@router.put("/api/profiles/{name}/sources/ytmusic/cookies")
def put_profile_ytmusic_cookies(name: str, body: dict, request: Request) -> dict:
    content = body.get("cookies_txt", "")
    try:
        _validate_cookies_txt(content, require_apple_token=False)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    profile = _profile(request, name)
    container_path = f"/config/secrets/{name}/youtube_cookies.txt"
    target = resolve_config_path(container_path, request.app.state.config_root)
    write_text_atomic(content, target)
    _save_with_ytmusic_override(request, profile, cookies_file=container_path)
    return {"status": "ok"}


@router.put("/api/profiles/{name}/sources/ytmusic/oauth-client")
def put_profile_ytmusic_oauth_client(name: str, body: dict, request: Request) -> dict:
    client_id = (body.get("client_id") or "").strip()
    client_secret = (body.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        raise HTTPException(status_code=422, detail="client_id and client_secret are both required")
    profile = _profile(request, name)
    container_path = f"/config/secrets/{name}/ytmusic_oauth_client.json"
    target = resolve_config_path(container_path, request.app.state.config_root)
    write_text_atomic(json.dumps({"client_id": client_id, "client_secret": client_secret}), target)
    _save_with_ytmusic_override(request, profile, oauth_client_file=container_path)
    return {"status": "ok"}


@router.post("/api/profiles/{name}/sources/ytmusic/oauth/start")
def start_profile_ytmusic_oauth(name: str, request: Request) -> dict:
    profile = _profile(request, name)
    global_config = _global_config(request)
    # The OAuth *client* can stay global/shared (one Google Cloud
    # project, reused by everyone) even when the resulting *token*
    # below is this profile's own -- which account you sign into
    # during the device-code flow is independent of which client
    # registration is asking. Falls back to global when this profile
    # has no client override of its own.
    client_path = resolve_ytmusic_oauth_client(profile, global_config, request.app.state.config_root)
    if not client_path.is_file():
        raise HTTPException(
            status_code=422,
            detail="no YouTube Music OAuth client saved yet -- set one up first "
            "(for this profile, or the shared global one)",
        )
    try:
        data = json.loads(client_path.read_text(encoding="utf-8"))
        client_id, client_secret = data["client_id"], data["client_secret"]
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"saved OAuth client file is unreadable: {e}") from e
    try:
        code = start_device_flow(client_id, client_secret)
    except OAuthFlowError as e:
        raise HTTPException(status_code=502, detail=f"could not start OAuth flow: {e}") from e
    return dataclasses.asdict(code)


@router.post("/api/profiles/{name}/sources/ytmusic/oauth/poll")
def poll_profile_ytmusic_oauth(name: str, body: dict, request: Request) -> dict:
    device_code = body.get("device_code", "")
    if not device_code:
        raise HTTPException(status_code=422, detail="device_code is required")
    profile = _profile(request, name)
    global_config = _global_config(request)
    client_path = resolve_ytmusic_oauth_client(profile, global_config, request.app.state.config_root)
    if not client_path.is_file():
        raise HTTPException(status_code=422, detail="no YouTube Music OAuth client saved yet")
    try:
        data = json.loads(client_path.read_text(encoding="utf-8"))
        client_id, client_secret = data["client_id"], data["client_secret"]
    except (json.JSONDecodeError, KeyError) as e:
        raise HTTPException(status_code=422, detail=f"saved OAuth client file is unreadable: {e}") from e
    try:
        token = poll_device_flow(client_id, client_secret, device_code)
    except OAuthPending:
        return {"status": "pending"}
    except OAuthFlowError as e:
        raise HTTPException(status_code=502, detail=f"OAuth flow failed: {e}") from e

    # A device-code sign-in for this profile always produces this
    # profile's own oauth_file -- even a shared/global client can sign
    # into a different account per profile, so the resulting token is
    # never written back to the shared global.yaml file.
    container_path = f"/config/secrets/{name}/ytmusic_oauth.json"
    target = resolve_config_path(container_path, request.app.state.config_root)
    write_text_atomic(json.dumps(token), target)
    _save_with_ytmusic_override(request, profile, oauth_file=container_path)
    return {"status": "ok"}


@router.post("/api/profiles/{name}/sources/{source}/import")
def import_profile_source(name: str, source: str, body: dict, request: Request) -> dict:
    if source not in ("apple_music", "ytmusic"):
        raise HTTPException(status_code=422, detail=f"unknown source {source!r}")
    from_profile_name = body.get("from_profile", "")
    if not from_profile_name:
        raise HTTPException(status_code=422, detail="from_profile is required")

    profile = _profile(request, name)
    from_profile = _profile(request, from_profile_name)
    global_config = _global_config(request)

    if source == "apple_music":
        container_path = _effective_apple_music_container_path(from_profile, global_config)
        _save_with_apple_music_override(request, profile, container_path)
    else:
        container_path = _effective_ytmusic_cookies_container_path(from_profile, global_config)
        _save_with_ytmusic_override(request, profile, cookies_file=container_path)
    return {"status": "ok"}


@router.delete("/api/profiles/{name}/sources/{source}")
def delete_profile_source_override(name: str, source: str, request: Request) -> dict:
    if source not in ("apple_music", "ytmusic"):
        raise HTTPException(status_code=422, detail=f"unknown source {source!r}")
    profile = _profile(request, name)
    if not profile.sources:
        return {"status": "ok"}
    updated_sources = profile.sources.model_copy(update={source: None})
    # If nothing is overridden any more, drop the whole block so it
    # doesn't linger in the YAML as an empty `sources: {}` -- matches
    # every other optional profile section's "absent means default"
    # convention (see ProfileSourcesConfig's docstring).
    if updated_sources.apple_music is None and updated_sources.ytmusic is None and updated_sources.spotify is None:
        updated_sources = None
    updated_profile = profile.model_copy(update={"sources": updated_sources})
    save_profile_config(updated_profile, _profile_path(request, name))
    return {"status": "ok"}
