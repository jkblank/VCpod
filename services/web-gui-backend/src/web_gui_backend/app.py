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

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web_gui_backend.routers import device, global_config, podcasts, profiles, sources

# Bound to localhost/LAN only per this build's locked-in access-control
# decision (no login system -- see notes.md's 2026-09-02 entry) -- CORS
# only needs to admit the frontend dev server's own origin(s), not the
# open internet.
_DEV_FRONTEND_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def create_app(config_root: Path | str, sync_orchestrator_dir: Path | str | None = None) -> FastAPI:
    app = FastAPI(title="VCpod web-gui-backend")
    app.state.config_root = Path(config_root)
    app.state.sync_orchestrator_dir = sync_orchestrator_dir

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_FRONTEND_ORIGINS,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router_module in (profiles, global_config, device, sources, podcasts):
        app.include_router(router_module.router)

    return app
