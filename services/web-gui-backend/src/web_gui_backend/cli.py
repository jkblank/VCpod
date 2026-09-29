from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from web_gui_backend.app import create_app, default_frontend_dist


def main() -> None:
    parser = argparse.ArgumentParser(prog="web-gui-backend")
    parser.add_argument(
        "--config-root",
        default="config",
        help="Root containing global.yaml and profiles/*.yaml (default 'config').",
    )
    parser.add_argument(
        "--library-root",
        default=None,
        help="Real host root containing music/, playlists/, podcasts/, "
        "audiobooks/. Defaults to a 'library' directory next to "
        "--config-root, same convention every other CLI here uses.",
    )
    parser.add_argument(
        "--sync-orchestrator-dir",
        default=None,
        help="Path to the sync-orchestrator project, used to invoke "
        "`sync-orchestrator identify-device` as a subprocess. Defaults to "
        "the sibling services/sync-orchestrator directory.",
    )
    parser.add_argument(
        "--state-root",
        default=None,
        help="Real host root containing state/*.sqlite, audiobooks/ beets "
        "db + discover state. Defaults to a 'state' directory next to "
        "--config-root, same convention every other CLI here uses.",
    )
    parser.add_argument(
        "--frontend-dist",
        default=None,
        help="Path to the frontend's built static assets (`npm run build`'s "
        "dist/ output). Defaults to the sibling services/web-gui-frontend/"
        "dist. When present, the backend serves the whole app on its own "
        "port -- no separate `npm run dev` needed. Absent entirely (e.g. "
        "never built), the backend just serves the JSON API as before.",
    )
    parser.add_argument(
        "--host-config-root",
        default=None,
        help="Real path to --config-root on the BARE HOST, only needed "
        "when this process itself is containerized (its own --config-root "
        "is then a container-internal bind-mount target, e.g. /config, "
        "not a path meaning anything to the systemd unit the auto-sync "
        "setup card generates -- that unit always runs on the bare "
        "host). Bare-metal deployments never need this.",
    )
    parser.add_argument(
        "--host-library-root",
        default=None,
        help="Same idea as --host-config-root, for --library-root.",
    )
    parser.add_argument(
        "--host-state-root",
        default=None,
        help="Same idea as --host-config-root, for --state-root.",
    )
    parser.add_argument(
        "--host-sync-orchestrator-dir",
        default=None,
        help="Real path, on the bare host, to a sync-orchestrator "
        "checkout with its own `uv sync`'d .venv -- the auto-sync "
        "systemd unit execs "
        "<this>/.venv/bin/sync-orchestrator directly, so this must be a "
        "real bare-metal install, never this container's own vendored "
        "copy (which only exists inside the container). Only needed "
        "when this process itself is containerized.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1 -- localhost only; this "
        "service has no login system, see notes.md, so only widen this "
        "to a LAN address deliberately).",
    )
    parser.add_argument("--port", type=int, default=8420)
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Dev only: auto-restart on code changes under this package's "
        "src/web_gui_backend/. Route/handler edits are picked up without "
        "manually killing and restarting the process -- see notes.md's "
        "2026-09-03 entry for why this used to require a manual restart "
        "(a stale process silently serving old routes, confirmed live).",
    )
    args = parser.parse_args()
    frontend_dist = Path(args.frontend_dist) if args.frontend_dist else default_frontend_dist()

    if args.reload:
        # See app.py::create_app_from_env's docstring for why env vars,
        # not plain args, are what actually reach the app on each reload.
        os.environ["WEB_GUI_CONFIG_ROOT"] = str(args.config_root)
        if args.sync_orchestrator_dir:
            os.environ["WEB_GUI_SYNC_ORCHESTRATOR_DIR"] = str(args.sync_orchestrator_dir)
        if args.library_root:
            os.environ["WEB_GUI_LIBRARY_ROOT"] = str(args.library_root)
        if args.state_root:
            os.environ["WEB_GUI_STATE_ROOT"] = str(args.state_root)
        if args.host_config_root:
            os.environ["WEB_GUI_HOST_CONFIG_ROOT"] = str(args.host_config_root)
        if args.host_library_root:
            os.environ["WEB_GUI_HOST_LIBRARY_ROOT"] = str(args.host_library_root)
        if args.host_state_root:
            os.environ["WEB_GUI_HOST_STATE_ROOT"] = str(args.host_state_root)
        if args.host_sync_orchestrator_dir:
            os.environ["WEB_GUI_HOST_SYNC_ORCHESTRATOR_DIR"] = str(args.host_sync_orchestrator_dir)
        os.environ["WEB_GUI_FRONTEND_DIST"] = str(frontend_dist)
        uvicorn.run(
            "web_gui_backend.app:create_app_from_env",
            factory=True,
            host=args.host,
            port=args.port,
            reload=True,
            reload_dirs=[str(Path(__file__).resolve().parent)],
        )
    else:
        app = create_app(
            args.config_root,
            args.sync_orchestrator_dir,
            args.library_root,
            frontend_dist,
            args.state_root,
            host_config_root=args.host_config_root,
            host_library_root=args.host_library_root,
            host_state_root=args.host_state_root,
            host_sync_orchestrator_dir=args.host_sync_orchestrator_dir,
        )
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
