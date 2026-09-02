"""FastAPI app: reads/writes the exact same config/ YAML files every CLI
tool uses, through the exact same common.config loader/writer -- no
parallel database, no reimplemented schema (see music-stack-planning.md
§7 and the session's plan for the full "config is the only source of
truth" reasoning this follows).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from common.config import (
    ConfigError,
    format_validation_error,
    load_all_profiles,
    load_global_config,
    load_profile_config,
    save_global_config,
    save_profile_config,
)
from common.models import GlobalConfig, ProfileConfig

from web_gui_backend.device import DeviceIdentifyError, identify_connected_devices

# Bound to localhost/LAN only per this build's locked-in access-control
# decision (no login system -- see notes.md's 2026-09-02 entry) -- CORS
# only needs to admit the frontend dev server's own origin(s), not the
# open internet.
_DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def _config_error_response(exc: ConfigError) -> HTTPException:
    return HTTPException(status_code=422, detail={"path": str(exc.path), "errors": exc.errors})


def _validate_or_422(model_cls: type[ProfileConfig] | type[GlobalConfig], body: dict, path: Path):
    try:
        return model_cls.model_validate(body)
    except ValidationError as e:
        raise _config_error_response(format_validation_error(path, e)) from e


def create_app(config_root: Path | str, sync_orchestrator_dir: Path | str | None = None) -> FastAPI:
    config_root = Path(config_root)
    profiles_dir = config_root / "profiles"
    global_config_path = config_root / "global.yaml"

    app = FastAPI(title="VCpod web-gui-backend")
    app.state.config_root = config_root
    app.state.sync_orchestrator_dir = sync_orchestrator_dir

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/api/profiles")
    def list_profiles() -> dict:
        # load_all_profiles silently returns {} for a directory that
        # doesn't exist (Path.glob on a missing dir just yields nothing,
        # no error) -- fine for the CLI loaders, which always run
        # against a config_root a human just typed and can see is wrong,
        # but here that would silently mask a bad --config-root as "zero
        # profiles" instead of the loud, obvious failure a wrong path
        # deserves. Confirmed live: this exact silent-empty response
        # masked a --config-root resolved relative to the wrong cwd,
        # while /api/global-config (which does check its target file's
        # existence) correctly errored on the very same misconfiguration
        # -- the inconsistency, not either check alone, was what made it
        # confusing to diagnose.
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
            raise _config_error_response(e) from e
        return {name: p.model_dump(mode="json") for name, p in profiles.items()}

    @app.get("/api/profiles/{name}")
    def get_profile(name: str) -> dict:
        path = profiles_dir / f"{name}.yaml"
        try:
            profile = load_profile_config(path)
        except ConfigError as e:
            if "file not found" in e.errors:
                raise HTTPException(status_code=404, detail=f"no profile named {name!r}") from e
            raise _config_error_response(e) from e
        return profile.model_dump(mode="json")

    @app.put("/api/profiles/{name}")
    def put_profile(name: str, body: dict) -> dict:
        if body.get("profile") != name:
            raise HTTPException(
                status_code=400,
                detail=f"body's profile field ({body.get('profile')!r}) must match "
                f"the URL name ({name!r})",
            )
        path = profiles_dir / f"{name}.yaml"
        profile = _validate_or_422(ProfileConfig, body, path)
        try:
            save_profile_config(profile, path)
        except ConfigError as e:
            raise _config_error_response(e) from e
        return profile.model_dump(mode="json")

    @app.delete("/api/profiles/{name}", status_code=204)
    def delete_profile(name: str) -> None:
        path = profiles_dir / f"{name}.yaml"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no profile named {name!r}")
        path.unlink()

    @app.get("/api/global-config")
    def get_global_config() -> dict:
        try:
            config = load_global_config(global_config_path)
        except ConfigError as e:
            raise _config_error_response(e) from e
        return config.model_dump(mode="json")

    @app.put("/api/global-config")
    def put_global_config(body: dict) -> dict:
        config = _validate_or_422(GlobalConfig, body, global_config_path)
        save_global_config(config, global_config_path)
        return config.model_dump(mode="json")

    @app.get("/api/device/identify")
    def identify_device(request: Request) -> dict:
        try:
            devices = identify_connected_devices(request.app.state.sync_orchestrator_dir)
        except DeviceIdentifyError as e:
            raise HTTPException(status_code=502, detail=str(e)) from e
        return {"devices": devices}

    return app
