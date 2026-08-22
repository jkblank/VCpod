# music-stack-cli

The single "sync everything" entrypoint — one call fetches every
configured playlist across every music source *and* every podcast show
for a profile, instead of invoking `fetcher-apple`/`fetcher-ytmusic`/
`podcast-manager` separately per playlist. Root workspace member;
depends on `common`, `fetcher-apple`, `fetcher-ytmusic`, and
`podcast-manager` directly (imports their `download`/`api` functions —
doesn't subprocess out to their own CLIs).

## Usage

```bash
uv run music-stack sync --profile config/profiles/<you>.yaml
```

Run from the repo root with a real `config/global.yaml` present, that's
the whole command — every other flag has a sensible default:

- `--global-config` (default `config/global.yaml`)
- `--config-root` (default: `--global-config`'s own parent directory) —
  what `/config/...`-prefixed credential paths in config resolve
  against.
- `--library-root` / `--state-root` (default: sibling `library`/`state`
  directories next to `--config-root`)
- `--source` (repeatable; default: every supported source) — restrict to
  `apple_music`, `ytmusic`, and/or `podcasts`. `spotify` is a recognized
  but explicitly **unsupported** choice here (see below), not silently
  ignored.
- `--playlist` (repeatable) — restrict to specific playlist name(s)
  across whichever sources are selected.
- `--show` (repeatable) — restrict podcast sync to specific show(s), by
  UUID or case-insensitive title.
- `--storefront` (default `us`) — Apple Music storefront.
- `--lock-timeout` (default 1800)

Per-item failures (one bad playlist, one unreachable show) are caught
and reported, not raised — the rest of the run continues. Unmatched
`--playlist`/`--show` names print a `WARNING`, not a hard failure
(likely a typo, not necessarily fatal).

**Spotify is explicitly out of scope for this command** —
`fetcher-spotify` is a standalone `uv` project with a separate,
heavier/pinned dependency tree kept isolated from the root workspace
(same reasoning as `sync-orchestrator`), and its downloads are
currently blocked on a Spotify Premium API requirement anyway (see
`services/fetcher-spotify/README.md`). `--source spotify` prints a
clear "not supported by this command yet" message rather than crashing
or silently doing nothing.

## Used internally, not just manually

Two other services call into this one rather than duplicating fetch
orchestration:

- **`fetch-scheduler`** imports `run_sync` directly (in-process) to
  drive its own cron-scheduled ticks — see
  `services/fetch-scheduler/README.md`.
- **`sync-orchestrator auto-sync`** (opportunistic pre-fetch step) and
  **`sync-orchestrator full-sync`** (its whole fetch stage) both shell
  out to `music-stack sync` as a **subprocess** (deliberately, not an
  import — keeps `sync-orchestrator`'s own `iopenpod`/PyQt6 dependency
  tree from merging with this one). See
  `services/sync-orchestrator/README.md`.
