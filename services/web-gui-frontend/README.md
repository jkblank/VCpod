# web-gui-frontend

React + Vite SPA for the web GUI (M11-M14) — talks to
[`services/web-gui-backend`](../web-gui-backend/README.md) over its
JSON REST API. Not part of the root `uv` workspace (this is a plain
`npm` project) — see `services/web-gui-backend/README.md` for the
Python side.

Not yet packaged/documented as part of "Running it" in the root
README — this is still an in-progress feature. See the root README's
Status table and `notes.md` for where M11-M14 actually stand.

## Setup

```bash
npm install
```

## Running it (dev)

Start the backend first (see `services/web-gui-backend/README.md`),
then:

```bash
npm run dev
```

Vite's dev server proxies `/api/*` straight to the backend at
`http://127.0.0.1:8420` (see `vite.config.ts`) — no CORS setup needed
for the common case. Visit `http://localhost:5173`.

## Building

```bash
npm run build
```

Type-checks (`tsc -b`) then bundles to `dist/` — this is what CI/a real
deploy would actually run; `npm run dev` never type-checks on its own.

For everyday use (as opposed to frontend development, where this dev
server's hot reload is worth keeping), you don't need this dev server
running at all once you've built once: `services/web-gui-backend`
serves this `dist/` directory itself when it exists, so `uv run
web-gui-backend` alone becomes "the whole app, one process, one port"
— see `services/web-gui-backend/README.md`'s "Running it as one
process".

## Where things live

- `src/api.ts` — typed fetch client for the backend's JSON API. Field
  shapes mirror `services/common/src/common/models.py`'s
  `ProfileConfig`/`GlobalConfig` exactly (via the backend's
  `model_dump(mode="json")`) — extend the types here as later screens
  need more fields; unmodeled fields pass through untouched via each
  type's index signature rather than getting silently dropped on save.
  Also home to `streamSSE`/`streamSyncPlan`/`streamSyncExecute` — a
  `fetch()` + `ReadableStream` reader that parses `event:`/`data:`
  frames manually (native `EventSource` is GET-only, can't carry a
  JSON body, so a POST that streams Server-Sent Events needs this
  instead — the standard workaround, not a new dependency).
- `src/screens/` — one component per screen: `Overview`, `Profiles`,
  `Sources` (Apple Music/YouTube Music playlist picker, plus "add a
  public playlist by link" for YouTube), `Podcasts` (Pocket Casts
  subscription picker + every `ProfilePodcastsConfig` setting —
  episode filter, fill_modes, fetch schedule, ...),
  `ExternalLibrary`/`Audiobooks` (browse a real directory tree and tick
  what to sync; `Audiobooks` also has a "Discover new audiobooks" card,
  global rather than per-profile, that scans a configurable drop-zone
  folder for raw parts-dirs and can kick off the merge+tag pipeline
  against one without leaving the browser), `Credentials` ("Sources &
  credentials" in the nav —
  per-source enable toggles + credential status, wiring the capture
  forms into a permanent home, including the YouTube Music OAuth
  device-code sign-in flow; each of Apple Music/YouTube Music also has
  a "For {profile}" sub-section — never auto-populated, only ever
  entered via "Set up separate credentials" or "Import from…" another
  profile, which points at that profile's exact file rather than
  copying it, plus "Revert to shared login" once overridden — see
  `common.models.ProfileSourcesConfig`), `Sync` (compute a real sync plan against
  a connected device via `/api/sync/plan`'s SSE stream, review it —
  sample track/playlist lists, storage delta, an explicit "I've
  reviewed the removals" checkbox whenever the plan proposes removing
  anything — then `/api/sync/execute` to actually write it; a
  "Dangerous mode" toggle skips straight to executing with removals
  allowed, no plan review, for when you already know what you want;
  also hosts the auto-sync setup card). Activity is still just the
  mockup's UX/copy spec (`docs/VCpod Console.html` at the repo root),
  not built here yet.
- `src/components/` — `CredentialWarning` (the big plaintext-storage
  warning every capture form shows), `CookieCaptureForm` (Apple
  Music/YouTube — paste or upload an already-exported `cookies.txt`;
  real cross-origin cookie *capture* isn't possible from a browser at
  all, see `notes.md`), `PocketCastsLoginForm` (validated against a
  real login before saving), `YtmusicOauthForm` (Google OAuth client
  capture, then the RFC 8628 device-code flow — shows the real
  verification URL + user code and polls until the backend confirms
  sign-in; the client/flow calls are injected as props, not hardcoded,
  so the same component drives both the global and each per-profile
  flow), `ImportOrRevertSource` (the "Import from…" dropdown /
  "Revert to shared login" button shared by Apple Music's and YouTube
  Music's per-profile sections), `ScheduleEditor` (cron-free schedule
  picker, backed by `cronBuilder.ts`), `DirectoryPicker` (breadcrumb
  directory browser shared by `ExternalLibrary`/`Audiobooks`),
  `AudiobookDiscovery` (the discover-and-process card described
  above), `AutoSyncSetupCard` (generated systemd unit/udev rule +
  install commands, read-only — no "install for me" button, this
  backend never runs privileged commands itself).
- `src/icons/` — the VCpod icon set, ported 1:1 from the "VCpod Icons"
  Claude Design project (Nocturne design system, imported via the
  `claude_design` MCP/`/design-login`): a shared clickwheel-ring motif
  (52-unit viewBox, 2px round-cap strokes) that every icon builds from.
  `Mark` is the bare logo (nav branding); `StateIcons.tsx` has the 12
  "keep the ring, replace the hub" states (`SyncedIcon`, `SyncingIcon`,
  `QueuedIcon`, `PausedIcon`, `NeedsAttentionIcon`, `UnreachableIcon`,
  `DeviceConnectedIcon`, `IdleIcon`, `ScheduledIcon`, `ToAddIcon`,
  `ToRemoveIcon`, `ProfileIcon`); `SourceIcons.tsx` has the 6 "hub
  becomes the object" icons (`StreamingPlaylistIcon`, `VideoSourceIcon`,
  `PodcastIcon`, `AudiobookIcon`, `ExternalLibraryIcon`,
  `CredentialsIcon`); `Spinner.tsx` has the three throbber variants
  (`Spinner` — Quadrant, the spec's own default for button/table waits;
  `DrawingSpinner`; `CounterRotatingSpinner`), which bucket their
  stroke width/hub visibility by `size` the same way the design's own
  three reference sizes do; `ProgressRing.tsx` is the determinate ring
  (`stroke-dashoffset = 100 - progress`, `pathLength=100` so the math
  stays exact regardless of radius). Colors reference the design's own
  `--color-accent-*`/`--color-neutral-*` CSS custom properties, added
  to `App.css`'s `:root` (`--color-accent` itself is just an alias for
  this app's existing `--accent` — the two hex values were already
  identical, this app's palette having been hand-matched to the same
  Nocturne system earlier). Wired into `App.tsx`'s nav (logo + one icon
  per screen), `Sync.tsx` (device status, button spinners, plan
  add/remove counts, the result banner), `AudiobookDiscovery.tsx`
  (processing spinner + per-book status), and `Credentials.tsx`
  (Apple Music/YouTube Music card status).
- `src/useProfileStore.ts` — "which profile is currently being edited"
  lifted out of any one screen into a shared hook — `App.tsx` calls it
  once and passes the same store down to `Profiles`/`Sources`/
  `Podcasts` as a prop, so a playlist ticked on the Sources screen
  mutates the exact same draft profile the Profiles screen edits.
- `src/App.tsx` — nav shell + screen switch (plain `useState`, no
  router yet — trivial to add once there are enough screens to warrant
  one). Shows "editing: {profile}" in the nav whenever a profile is
  selected, since picker actions are otherwise easy to lose track of.
