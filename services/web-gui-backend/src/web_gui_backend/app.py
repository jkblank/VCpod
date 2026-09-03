"""FastAPI app: reads/writes the exact same config/ YAML files every CLI
tool uses, through the exact same common.config loader/writer -- no
parallel database, no reimplemented schema (see music-stack-planning.md
§7 and the session's plan for the full "config is the only source of
truth" reasoning this follows).

Routes live in routers/ (one module per resource) -- this file is just
the factory that wires config_root/sync_orchestrator_dir into app.state
and mounts each router. See errors.py for the shared
ConfigError/ValidationError -> HTTP response helpers every router uses.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from web_gui_backend.routers import (
    activity,
    alerts,
    audiobooks,
    audiobooks_discover,
    auto_sync_setup,
    device,
    external_library,
    global_config,
    overview,
    podcasts,
    profile_sources,
    profiles,
    sources,
    sync,
)

# Bound to localhost/LAN only per this build's locked-in access-control
# decision (no login system -- see notes.md's 2026-09-02 entry) -- CORS
# only needs to admit the frontend dev server's own origin(s), not the
# open internet.
_DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def default_frontend_dist() -> Path:
    """Absolute path to the sibling web-gui-frontend's build output,
    derived from this installed package's own location -- same pattern
    device.py's _default_sync_orchestrator_dir() already uses. Deliberately
    NOT applied automatically inside create_app()/create_app_from_env()
    below -- those stay explicit (frontend_dist=None means "don't mount
    anything", full stop) so every test calling create_app() directly
    stays hermetic instead of silently picking up whatever this real
    repo's own dist/ happens to contain. cli.py is the one real caller
    that wants this convenience, and computes it explicitly."""
    return Path(__file__).resolve().parents[3] / "web-gui-frontend" / "dist"


def create_app(
    config_root: Path | str,
    sync_orchestrator_dir: Path | str | None = None,
    library_root: Path | str | None = None,
    frontend_dist: Path | str | None = None,
    state_root: Path | str | None = None,
) -> FastAPI:
    config_root = Path(config_root)
    app = FastAPI(title="VCpod web-gui-backend")
    app.state.config_root = config_root
    app.state.sync_orchestrator_dir = sync_orchestrator_dir
    # Same default every other CLI here uses: a sibling 'library'/'state'
    # directory next to config_root.
    app.state.library_root = Path(library_root) if library_root else config_root.parent / "library"
    app.state.state_root = Path(state_root) if state_root else config_root.parent / "state"

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router_module in (
        profiles,
        global_config,
        device,
        sources,
        profile_sources,
        podcasts,
        external_library,
        audiobooks,
        audiobooks_discover,
        sync,
        auto_sync_setup,
        alerts,
        overview,
        activity,
    ):
        app.include_router(router_module.router)

    # Mounted last and at "/" deliberately -- Starlette matches routes
    # in registration order, so every /api/... route (and FastAPI's own
    # /docs, /openapi.json, registered during __init__ above) is tried
    # first; only a path none of those match falls through to this
    # static mount. html=True serves index.html for "/" -- the frontend
    # has no client-side router yet (plain useState screen switch), so
    # that's the only path that needs serving; an unknown path 404s,
    # same as before this existed. frontend_dist=None (the default)
    # mounts nothing -- see default_frontend_dist()'s docstring for why
    # this doesn't auto-detect the real repo's dist/ on its own.
    if frontend_dist and Path(frontend_dist).is_dir():
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")

    return app


# uvicorn --reload re-execs and re-imports the app fresh in a subprocess
# on every file change under reload_dirs -- it can only do that from an
# import string ("module:factory_name"), never an already-constructed
# app object, so config_root/library_root/sync_orchestrator_dir can't
# be passed as plain Python call args the way create_app() takes them
# for a normal run. Env vars are the one channel that survives across
# that re-exec; cli.py's --reload flag sets these before handing this
# factory's dotted path to uvicorn.
def create_app_from_env() -> FastAPI:
    return create_app(
        config_root=os.environ.get("WEB_GUI_CONFIG_ROOT", "config"),
        sync_orchestrator_dir=os.environ.get("WEB_GUI_SYNC_ORCHESTRATOR_DIR") or None,
        library_root=os.environ.get("WEB_GUI_LIBRARY_ROOT") or None,
        frontend_dist=os.environ.get("WEB_GUI_FRONTEND_DIST") or None,
        state_root=os.environ.get("WEB_GUI_STATE_ROOT") or None,
    )
