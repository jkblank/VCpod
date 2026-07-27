# common

Shared library every other service in this repo depends on — not a
fetcher or a sync tool itself, just the config schema, state db, and a
handful of small pure-logic modules everything else builds on. Root
workspace member; standalone projects (`fetcher-spotify`,
`sync-orchestrator`) depend on it via `{ path = "../common" }` instead
of `{ workspace = true }` — see "Standalone-project gotcha" below.

## What's in here

- **`models.py`** — every Pydantic config model (`GlobalConfig`,
  `ProfileConfig`, `PlaylistEntry`, etc.), all `extra="forbid"` so a typo
  in YAML fails loudly instead of silently doing nothing. `CronSchedule`
  is a shared, croniter-validated type used everywhere a cron expression
  appears in config.
- **`config.py`** — `load_global_config`/`load_profile_config`/
  `load_all_profiles`: plain `yaml.safe_load` + Pydantic validation,
  wrapped into `ConfigError` with per-field messages. `profile: global`
  is a reserved name (kept free for cross-profile maintenance state).
- **`state.py`** — `StateDB`, one SQLite file per profile
  (`state/{profile}.sqlite`): tracks/episodes tables (source-id → local
  file map, download history) plus `fetch_runs` (last-fetch tracking for
  the scheduler).
- **`schedule.py`** — pure due/not-due logic for cron-scheduled fetches
  (`is_due`, `is_due_within`, `iter_fetch_targets`,
  `resolve_fetch_scope`) — no I/O, fully unit-testable.
- **`backups.py`** — device backup snapshot retention/garbage collection
  (`resolve_retention_map`, `prune_and_gc_backups`). Deliberately has
  **no** `iopenpod` dependency even though it operates on
  `sync-orchestrator`'s backup data — it only reads the plain JSON
  manifest + content-addressed blob files on disk, so it's usable from
  both `fetch-scheduler` (root workspace) and standalone
  `sync-orchestrator` without either pulling in the other's dependency
  tree.
- **`lock.py`** — `FileLock`, `fcntl.flock`-based cross-process advisory
  locking, used everywhere something shouldn't run twice concurrently
  (a source's fetch session, a profile's sync, the maintenance tasks).
- **`playlist.py`** — `write_m3u8`, shared by every fetcher so `.m3u8`
  output format/absolute-path handling is defined exactly once.

## Usage

Every other service imports this directly — there's rarely a reason to
invoke it on its own, except one small standalone check:

```bash
uv run music-stack-validate [repo-root]
```

Loads `config/global.yaml` and every `config/profiles/*.yaml` under the
given root (defaults to the current directory) and reports `OK`/`ERROR`
per file — a quick way to confirm config is valid before running
anything real against it, without needing a specific service's own
flags.

## Standalone-project gotcha

`fetcher-spotify` and `sync-orchestrator` aren't root-workspace members
(each has a dependency — `zotify`/`librespot`, `iopenpod`/PyQt6 — heavy
or conflicting enough to keep isolated), so they depend on `common` via
a plain path reference instead of `{ workspace = true }`. That means
their own `.venv` doesn't automatically pick up changes to `common`'s
source the way root-workspace members do — after editing anything under
`services/common/src/common/`, run `uv sync --reinstall-package common`
inside each standalone project's own directory, or you'll hit a
confusing `TypeError: unexpected keyword argument` / `ModuleNotFoundError`
against code that clearly has the change. See `notes.md` for the full
history of this biting more than once.
