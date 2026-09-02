# web-gui-backend

FastAPI service backing the web GUI (M11-M14) — reads/writes the exact
same `config/*.yaml` files every CLI tool uses, through the exact same
`common.config` loader/writer, so the GUI is a thin layer over one
source of truth rather than a parallel system with its own state. Root
workspace member (unlike `sync-orchestrator`/`fetcher-spotify`) — see
"Architecture" below for why.

## Usage

```bash
uv run web-gui-backend --config-root config
```

- `--config-root` (default `config`) — same convention every other
  service's `--config-root` uses.
- `--sync-orchestrator-dir` (default: sibling `services/sync-orchestrator`,
  derived from this package's own install location) — where to find
  `sync-orchestrator identify-device` to shell out to.
- `--host` (default `127.0.0.1`) — **only widen this deliberately.**
  This service has no login system (see "Security posture" below); it's
  meant to stay on localhost or your own LAN, never the open internet.
- `--port` (default `8420`)

## API

All JSON, no HTML — the frontend (`services/web-gui-frontend`) is a
separate React SPA, not server-rendered.

| Method | Path | What |
|---|---|---|
| GET | `/api/profiles` | Every profile, keyed by name (`common.config.load_all_profiles`) |
| GET | `/api/profiles/{name}` | One profile, 404 if it doesn't exist |
| PUT | `/api/profiles/{name}` | Create or overwrite — body's `profile` field must match `{name}` |
| DELETE | `/api/profiles/{name}` | Deletes `config/profiles/{name}.yaml` |
| GET | `/api/global-config` | `config/global.yaml` |
| PUT | `/api/global-config` | Overwrites `config/global.yaml` |
| GET | `/api/device/identify` | Shells out to `sync-orchestrator identify-device`, returns `{"devices": [...]}` |

A validation failure (bad enum value, missing required field, a
profile named the reserved `"global"`, a duplicate profile name across
`config/profiles/*.yaml`, ...) returns **422** with
`{"path": "...", "errors": ["dotted.field.path — message", ...]}` —
the same `(path, errors)` shape `common.config.ConfigError` already
carries everywhere else in this project, so a frontend can map each
message onto a form field by splitting on `.`.

A failed `identify-device` subprocess call (uv/sync-orchestrator not
set up, etc.) returns **502** with a plain string detail, not a crash.

## Architecture

- **Root workspace member**, not standalone — imports `common` directly
  (in-process) for config load/save. This will grow to import
  `fetcher-apple`/`fetcher-ytmusic`/`podcast-manager`/`music-stack-cli`/
  `fetch-scheduler`/`library-manager` too as M12-M14 add playlist/show
  picking, but nothing beyond `common` is needed yet.
- **`sync-orchestrator` stays a subprocess call** (`device.py`,
  `identify_connected_devices`), same reasoning
  `sync-orchestrator`'s own `_build_music_stack_fetch_cmd` already
  documents for the reverse direction: it's a standalone `uv` project
  specifically so its `iopenpod`/PyQt6 dependency tree never merges
  with this (or any other root-workspace) service's.
- **Config is the only source of truth** — no database, no cached
  copy. Every route reads/writes through `common.config`'s
  `load_*`/`save_*` functions, the same ones every CLI tool already
  uses.

## Security posture (deliberate, see `notes.md`'s 2026-09-02 entry)

- No login system — access control is "don't expose this beyond
  localhost/your LAN," not app-level auth. Appropriate for a
  single-user personal tool; revisit if that ever changes.
- Credentials this service will eventually accept (Pocket Casts
  email/password, Apple Music/YouTube cookies — not yet built, see
  M12) stay plaintext under `config/secrets/`, same posture as every
  CLI tool today. Not encrypted at rest.
- Nothing here executes privileged commands. Auto-sync setup (planned,
  M14) will generate the filled-in systemd unit/udev rule files and
  display the exact `sudo` commands for a human to run — never attempt
  to run them itself.

## Tests

```bash
uv run pytest services/web-gui-backend
```

`tests/test_profiles.py` uses FastAPI's `TestClient` against a real
`create_app(config_root=tmp_path)` — no mocking of `common.config`,
real YAML files written and read back. `tests/test_device.py` mocks
`subprocess.run` only (never actually shells out to
`sync-orchestrator` in tests).
