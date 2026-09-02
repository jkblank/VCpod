from __future__ import annotations

import argparse

import uvicorn

from web_gui_backend.app import create_app


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
        "--host",
        default="127.0.0.1",
        help="Bind address (default 127.0.0.1 -- localhost only; this "
        "service has no login system, see notes.md, so only widen this "
        "to a LAN address deliberately).",
    )
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    app = create_app(args.config_root, args.sync_orchestrator_dir, args.library_root)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
