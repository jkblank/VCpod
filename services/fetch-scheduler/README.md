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

Each tick also runs library maintenance as a post-step whenever any
profile actually fetched: cross-source dedup, quarantine cleanup
(`library-manager`), and device backup pruning/GC (`common.backups`).
These have no schedule of their own — gated purely by
`config/global.yaml`'s `library_manager.dedup_enabled`/`cleanup_enabled`
and `backups.prune_enabled` booleans, off by default. See "Library
maintenance" below.

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

## Library maintenance

```yaml
# config/global.yaml
library_manager:
  dedup_enabled: true     # library-manager dedup, whenever any fetch happens
  cleanup_enabled: true   # library-manager cleanup-duplicates, same trigger
  fuzzy_threshold: 92.0             # optional, matches library-manager's own default
  quarantine_older_than_days: 14    # optional, ditto
backups:
  prune_enabled: true               # prune + GC state/device_backups/, same trigger
  default_keep_last: 3              # per device_backups/{device_id}, unless a
  default_max_age_days: 14          # profile overrides via its own `backups:` block
```

All three run **at most once per tick**, as a single post-step after
the per-profile loop, gated only by these booleans — not their own cron
schedule, since they're global (cross-profile) operations that don't
belong to any one profile's fetch cadence. `backup_prune` resolves each
`state/device_backups/{device_id}/` directory to a profile's retention
policy without needing a live device connection (matches by serial, or
by sampling a snapshot's recorded device name against a profile's
configured volume label — see `common/backups.py`); a directory that
matches no profile still gets the global default policy, never left
unmanaged. `--dry-run` reports exactly what each task would do (tracks
scanned, snapshots/blobs that would be deleted, bytes freed) without
touching anything — worth running once before flipping any of these
booleans on for real, since backup pruning is the one destructive
operation in this whole pipeline.

**Two things worth knowing before relying on `keep_last`/`max_age_days`
alone to bound disk usage** (found live, 2026-08-21, trimming a real
313GB `device_backups/` down to 134GB): first, retention is keyed by
`device_id` directory, and the *same physical device* can accumulate
more than one `device_id` across sessions (a different serial/FireWire-
GUID reading, or an old profile `device.match_value` that's since
changed) — each stale alias directory gets pruned independently under
the default policy and won't be recognized as "the same device" to
merge with the current one, so real orphaned history can sit there
indefinitely unless you notice and clean it up by hand. Second, because
`blobs/` is content-addressed, a heavy re-tag of the library (e.g. an
embedded-artwork resize touching every file) changes every file's hash
at once — the next snapshot then shares almost no blobs with the
previous one, so `keep_last=1` frees far less than you'd expect right
after that kind of bulk change, since the single kept snapshot is
itself just genuinely large. Neither is a bug in `prune_and_gc_backups`
— both are real properties of a content-addressed store, worth knowing
before assuming a tight `keep_last` alone caps disk usage.
