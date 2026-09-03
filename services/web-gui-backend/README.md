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
- `--library-root` (default: sibling `library` next to `--config-root`,
  same convention every other CLI here uses) — where `library/
  audiobooks` actually is, for the Audiobooks browse route.
- `--state-root` (default: sibling `state` next to `--config-root`,
  same convention) — where `audiobook-manager`'s beets db and discover
  state (`state/audiobooks/discovered_state.json`) live, for the
  audiobook-discover routes below.
- `--sync-orchestrator-dir` (default: sibling `services/sync-orchestrator`,
  derived from this package's own install location) — where to find
  `sync-orchestrator identify-device` to shell out to.
- `--frontend-dist` (default: sibling `services/web-gui-frontend/dist`) —
  when this directory exists (i.e. `npm run build` has been run over
  there), this one process serves the built SPA *and* the JSON API
  together on `--port`, no separate `npm run dev`/static server needed.
  When it doesn't exist yet, this is a no-op — the backend just serves
  the API as before. See "Running it as one process" below.
- `--host` (default `127.0.0.1`) — **only widen this deliberately.**
  This service has no login system (see "Security posture" below); it's
  meant to stay on localhost or your own LAN, never the open internet.
- `--port` (default `8420`)
- `--reload` (dev only) — auto-restarts on code changes under this
  package's `src/web_gui_backend/`, so a route/handler edit is picked
  up without manually killing and restarting the process. Confirmed
  live: without this, a long-running process silently keeps serving
  its old routes after a `git pull`/new commit — every newly-added
  route 404s (not 500, not a validation error — genuinely "route not
  found", since the running process never re-imports anything on its
  own) until it's restarted. See `notes.md`'s 2026-09-03 entry.

  ```bash
  uv run web-gui-backend --config-root config --reload
  ```

## Running it as one process

For everyday use (not frontend development, where the Vite dev server's
hot reload is worth keeping — see `services/web-gui-frontend/README.md`),
build the frontend once and just run the backend:

```bash
cd services/web-gui-frontend && npm run build && cd ../..
uv run web-gui-backend --config-root config
```

Visit `http://127.0.0.1:8420/` — the backend serves the built SPA
itself (`fastapi.staticfiles.StaticFiles`, mounted at `/` *after* every
`/api/...` route, so API routes always take priority) alongside the
JSON API on the same port. No CORS setup needed since it's all one
origin. This is "one thing to run" for setup purposes; see notes.md's
"package the web GUI as one thing to run" entry for the heavier
options (a single Docker image, an installer, ...) still being
weighed for later.

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
| GET | `/api/sources/apple-music/playlists` | Lists the Apple Music account's playlists (`fetcher_apple.api.list_playlists`) |
| GET | `/api/sources/ytmusic/playlists` | Lists the YouTube Music account's playlists |
| GET | `/api/sources/ytmusic/resolve?url=...` | Resolves a public playlist by share link or bare id — works unauthenticated, for playlists not saved to your own account |
| PUT | `/api/sources/apple-music/cookies` | Body `{"cookies_txt": "..."}` — validated (Netscape format + `media-user-token` present), written atomically |
| PUT | `/api/sources/ytmusic/cookies` | Body `{"cookies_txt": "..."}` — validated (Netscape format), written atomically |
| GET | `/api/sources/status` | Per-source status — `updated_at` is the credential file's real mtime, not a guessed expiry. `apple_music`/`spotify`: `{enabled, exists, updated_at}`. `ytmusic`: `{enabled, cookies: {...}, oauth: {...}, oauth_client: {...}}` — three independent credentials reported separately (cookies for every download, oauth for private-playlist listing, oauth_client is the Google OAuth client the oauth token needs for auto-refresh) |
| PUT | `/api/sources/ytmusic/oauth-client` | Body `{"client_id", "client_secret"}` — the user's own Google OAuth client (ytmusicapi has no shared/default one), written atomically. Prerequisite for the device-code flow below, and also read back on every playlist-listing call so a captured token can auto-refresh instead of breaking on expiry |
| POST | `/api/sources/ytmusic/oauth/start` | Starts the RFC 8628 device-code flow — returns `{device_code, user_code, verification_url, expires_in, interval}`. 422 if no OAuth client saved yet |
| POST | `/api/sources/ytmusic/oauth/poll` | Body `{"device_code"}` — `{"status": "pending"}` while the user hasn't finished the browser step yet (poll again after `interval` seconds), `{"status": "ok"}` and `oauth_file` written atomically on success, 502 on a real failure (expired code, denied, bad client) |
| GET | `/api/profiles/{name}/pocketcasts-status` | `{exists, updated_at}` for that profile's saved Pocket Casts credentials |
| GET | `/api/profiles/{name}/pocketcasts/subscriptions` | That profile's real Pocket Casts subscriptions (requires credentials already saved) |
| PUT | `/api/profiles/{name}/pocketcasts-credentials` | Body `{"email", "password"}` — validated via a real Pocket Casts login *before* writing anything |
| GET | `/api/external-library/browse?root=...&subpath=...` | Lists one directory under an arbitrary, user-supplied root (`ExternalLibraryConfig.path`) — the one route that reads a filesystem location this project doesn't otherwise manage, so every listing is confined to `root` (see `browse.py`) |
| GET | `/api/audiobooks/browse?subpath=...` | Lists one directory under `library_root/audiobooks` (resolved internally, never client-supplied) |
| GET | `/api/audiobooks/discover` | Scans `global.yaml`'s `audiobook_manager.discover_root` for raw, not-yet-processed parts-dirs (`audiobook_manager.discover.discover_audiobooks`), flagging which ones a previous import already handled. `{"root": "", "books": []}` when `discover_root` isn't set yet — not an error |
| POST | `/api/audiobooks/discover/import` | Body `{"name"}` — runs the real merge+tag pipeline (`audiobook_manager.pipeline.run_import_audiobook`, the same code `audiobook-manager import-audiobook` uses) against `{discover_root}/{name}`. 502 on a real failure (no ffmpeg, beets crashing); 422 if beets-audible couldn't confidently match the book (not a failure — the merged file is left in place for a manual `metadata.yml` retry, see `services/audiobook-manager/README.md`); `{"status": "ok", "imported_paths": [...]}` on success |

A validation failure (bad enum value, missing required field, a
profile named the reserved `"global"`, a duplicate profile name across
`config/profiles/*.yaml`, ...) returns **422** with
`{"path": "...", "errors": ["dotted.field.path — message", ...]}` —
the same `(path, errors)` shape `common.config.ConfigError` already
carries everywhere else in this project, so a frontend can map each
message onto a form field by splitting on `.`.

A failed `identify-device`/playlist-listing/subscription-listing call
(source not authenticated, network error, etc.) returns **502** with a
plain string detail, not a crash — these all reach out to a real
external system whose failure modes aren't enumerable up front.

Cookie/credential writes never echo content back — a successful `PUT`
just returns `{"status": "ok"}`. A bad cookie paste or a rejected
Pocket Casts login never touches the real file/never gets written at
all (validated first; cookie writes go through a temp-file-then-rename
so a working file is never left corrupted mid-write, same pattern
`podcast_manager/download.py::_download_enclosure` already uses).

## Architecture

- **Root workspace member**, not standalone — imports `common`,
  `fetcher-apple`, `fetcher-ytmusic`, `podcast-manager`,
  `audiobook-manager` directly (in-process); `audiobook-manager` being a
  root-workspace member itself (unlike `sync-orchestrator`/
  `fetcher-spotify`) is exactly why this is safe — beets-audible is
  already installed in this venv, not a new isolated dependency tree.
  `music-stack-cli`/`fetch-scheduler`/`library-manager` aren't needed
  yet (M14 territory).
- **`sync-orchestrator` stays a subprocess call** (`device.py`,
  `identify_connected_devices`), same reasoning
  `sync-orchestrator`'s own `_build_music_stack_fetch_cmd` already
  documents for the reverse direction: it's a standalone `uv` project
  specifically so its `iopenpod`/PyQt6 dependency tree never merges
  with this (or any other root-workspace) service's.
- **Config is the only source of truth** — no database, no cached
  copy. Every route reads/writes through `common.config`'s
  `load_*`/`save_*` functions, the same ones every CLI tool already
  uses. Global-config credential paths (`/config/...`-container-style)
  resolve through `common.config.resolve_config_path` — the same
  resolver `music-stack-cli`'s own fetch pipeline uses.
- **One router module per resource** (`routers/profiles.py`,
  `global_config.py`, `device.py`, `sources.py`, `podcasts.py`) —
  `app.py` is just the `create_app()` factory that wires
  `config_root`/`sync_orchestrator_dir` into `app.state` and mounts
  each router. Shared error-response helpers live in `errors.py`, the
  shared atomic-write helper in `atomic.py`.

## Security posture (deliberate, see `notes.md`'s 2026-09-02 entries)

- No login system — access control is "don't expose this beyond
  localhost/your LAN," not app-level auth. Appropriate for a
  single-user personal tool; revisit if that ever changes.
- Credentials (Pocket Casts email/password, Apple Music/YouTube
  cookies, YouTube Music's OAuth client secret and captured token) stay
  plaintext under `config/secrets/`, same posture as every CLI tool
  today. Not encrypted at rest — a deliberate, separate,
  not-yet-scheduled follow-up if it ever happens.
- Nothing here executes privileged commands. Auto-sync setup (planned,
  M14) will generate the filled-in systemd unit/udev rule files and
  display the exact `sudo` commands for a human to run — never attempt
  to run them itself.

## Tests

```bash
uv run pytest services/web-gui-backend
```

Every test file uses FastAPI's `TestClient` against a real
`create_app(config_root=tmp_path)` — no mocking of `common.config`,
real YAML files written and read back. External-system calls (Apple
Music/YouTube Music/Pocket Casts APIs, `sync-orchestrator`'s
subprocess) are mocked at the module boundary
(`monkeypatch.setattr(routers.sources, "list_apple_music_playlists",
...)` etc.) — but cookie *validation* itself is exercised for real
against small synthetic Netscape-format fixture strings, not mocked,
since that's exactly the logic most worth catching a regression in.
