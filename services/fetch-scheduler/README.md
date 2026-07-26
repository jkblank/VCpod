# fetch-scheduler

Keeps `library/` fresh on each playlist's/podcast show's configured
`fetch_schedule` (a cron expression — see `music-stack-planning.md` §3 and
`config/profiles/alice.yaml` for the schema), independently of whether any
iPod is connected. This is what lets `sync-orchestrator`'s udev-triggered
`auto-sync` (bare metal, separate service) stay fast — it just writes
whatever's already here, only doing a short opportunistic pre-fetch when a
scheduled fetch is about to happen anyway.

Reuses `music_stack_cli.orchestrate.run_sync` for the actual fetching —
this service only adds the "which targets are due, and when" layer on top
(`common.schedule`, `common.state`'s `fetch_runs` table).

## Deployment

Two equally-supported ways to run it, pick whichever fits your setup:

1. **Long-running process** (e.g. the `docker-compose.yml` service):
   ```
   fetch-scheduler --config-root config --tick-seconds 60
   ```
   Loops forever, checking for due targets every `--tick-seconds`.

2. **Cron / systemd timer**, invoking a single pass instead:
   ```
   fetch-scheduler --config-root config --once
   ```
   Exits after one tick (nonzero exit code if any profile's tick raised an
   unexpected error — see logs for detail). Schedule this yourself via
   cron/systemd at whatever interval you want ticks to happen.

## Flags

- `--config-root` (default `config`) — expects `global.yaml` and
  `profiles/*.yaml` under here.
- `--library-root` / `--state-root` (default: sibling `library`/`state`
  directories next to `--config-root`).
- `--tick-seconds` (default 60) — only used in long-running mode.
- `--once` — single tick then exit (see above).
- `--dry-run` — print which targets are due without fetching anything or
  writing to `fetch_runs`. Useful to sanity-check resolved schedules
  before a real run.
- `--lock-timeout` (default 1800) — passed through to `run_sync`'s
  per-source locks and to the per-profile fetch lock this service takes
  (`.fetch_{profile}.lock`) to avoid racing a manual `music-stack sync`
  run or another tick.
