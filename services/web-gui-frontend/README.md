# web-gui-frontend

React + Vite SPA for the web GUI (M11-M14) — talks to
[`services/web-gui-backend`](../web-gui-backend/README.md) over its
JSON REST API. Not part of the root `uv` workspace (this is a plain
`npm` project) — see `services/web-gui-backend/README.md` for the
Python side.

Not yet packaged/documented as part of "Running it" in the root
README — this is still M11's proof-of-round-trip scaffold (Overview +
Profiles screens only), not a finished feature. See the root README's
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

## Where things live

- `src/api.ts` — typed fetch client for the backend's JSON API. Field
  shapes mirror `services/common/src/common/models.py`'s
  `ProfileConfig`/`GlobalConfig` exactly (via the backend's
  `model_dump(mode="json")`) — extend the types here as later screens
  need more fields; unmodeled fields pass through untouched via each
  type's index signature rather than getting silently dropped on save.
- `src/screens/` — one component per screen. Only `Overview.tsx` and
  `Profiles.tsx` exist so far (M11's scope). The rest of the mockup's
  screens (`docs/VCpod Console.html` at the repo root — Music sources
  picker, External library, Podcasts, Audiobooks, Sync, Activity,
  Sources & credentials) are the UX/copy spec for M12-M14, not yet
  built here.
- `src/App.tsx` — nav shell + screen switch (plain `useState`, no
  router yet — trivial to add once there are enough screens to warrant
  one).
