from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from common.config import ConfigError, load_all_profiles, load_profile_config, save_profile_config
from common.models import ProfileConfig

from web_gui_backend.errors import config_error_response, validate_or_422

router = APIRouter()


@router.get("/api/profiles")
def list_profiles(request: Request) -> dict:
    profiles_dir = request.app.state.config_root / "profiles"
    # load_all_profiles silently returns {} for a directory that doesn't
    # exist (Path.glob on a missing dir just yields nothing, no error) --
    # fine for the CLI loaders, which always run against a config_root a
    # human just typed and can see is wrong, but here that would silently
    # mask a bad --config-root as "zero profiles" instead of the loud,
    # obvious failure a wrong path deserves. Confirmed live: this exact
    # silent-empty response masked a --config-root resolved relative to
    # the wrong cwd, while /api/global-config (which does check its
    # target file's existence) correctly errored on the very same
    # misconfiguration -- the inconsistency, not either check alone, was
    # what made it confusing to diagnose.
    if not profiles_dir.is_dir():
        raise HTTPException(
            status_code=422,
            detail={
                "path": str(profiles_dir),
                "errors": ["directory not found — check --config-root"],
            },
        )
    try:
        profiles = load_all_profiles(profiles_dir)
    except ConfigError as e:
        raise config_error_response(e) from e
    return {name: p.model_dump(mode="json") for name, p in profiles.items()}


@router.get("/api/profiles/{name}")
def get_profile(name: str, request: Request) -> dict:
    path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    try:
        profile = load_profile_config(path)
    except ConfigError as e:
        if "file not found" in e.errors:
            raise HTTPException(status_code=404, detail=f"no profile named {name!r}") from e
        raise config_error_response(e) from e
    return profile.model_dump(mode="json")


@router.put("/api/profiles/{name}")
def put_profile(name: str, body: dict, request: Request) -> dict:
    if body.get("profile") != name:
        raise HTTPException(
            status_code=400,
            detail=f"body's profile field ({body.get('profile')!r}) must match "
            f"the URL name ({name!r})",
        )
    path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    profile = validate_or_422(ProfileConfig, body, path)
    try:
        save_profile_config(profile, path)
    except ConfigError as e:
        raise config_error_response(e) from e
    return profile.model_dump(mode="json")


@router.delete("/api/profiles/{name}", status_code=204)
def delete_profile(name: str, request: Request) -> None:
    path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}")
    path.unlink()
