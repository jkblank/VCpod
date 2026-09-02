# web-gui-frontend

React + Vite SPA for the web GUI (M11-M14) — talks to
[`services/web-gui-backend`](../web-gui-backend/README.md) over its
JSON REST API. Not part of the root `uv` workspace (this is a plain
`npm` project) — see `services/web-gui-backend/README.md` for the
Python side.

Not yet packaged/documented as part of "Running it" in the root
README — this is still an in-progress feature (M12, scoped as "M12a" —
playlist/podcast picking + credential capture — see `notes.md`), not a
finished one. See the root README's Status table for where M11-M14
actually stand.

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

## Where things live

- `src/api.ts` — typed fetch client for the backend's JSON API. Field
  shapes mirror `services/common/src/common/models.py`'s
  `ProfileConfig`/`GlobalConfig` exactly (via the backend's
  `model_dump(mode="json")`) — extend the types here as later screens
  need more fields; unmodeled fields pass through untouched via each
  type's index signature rather than getting silently dropped on save.
- `src/screens/` — one component per screen: `Overview`, `Profiles`,
  `Sources` (Apple Music/YouTube Music playlist picker), `Podcasts`
  (Pocket Casts subscription picker). External library, Audiobooks,
  Sync, Activity, and the polished Sources & credentials status screen
  are still just the mockup's UX/copy spec (`docs/VCpod Console.html`
  at the repo root), not built here yet.
- `src/components/` — `CredentialWarning` (the big plaintext-storage
  warning every capture form shows), `CookieCaptureForm` (Apple
  Music/YouTube — paste or upload an already-exported `cookies.txt`;
  real cross-origin cookie *capture* isn't possible from a browser at
  all, see `notes.md`), `PocketCastsLoginForm` (validated against a
  real login before saving).
- `src/useProfileStore.ts` — "which profile is currently being edited"
  lifted out of any one screen into a shared hook — `App.tsx` calls it
  once and passes the same store down to `Profiles`/`Sources`/
  `Podcasts` as a prop, so a playlist ticked on the Sources screen
  mutates the exact same draft profile the Profiles screen edits.
- `src/App.tsx` — nav shell + screen switch (plain `useState`, no
  router yet — trivial to add once there are enough screens to warrant
  one). Shows "editing: {profile}" in the nav whenever a profile is
  selected, since picker actions are otherwise easy to lose track of.
