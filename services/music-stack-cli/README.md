# music-stack-cli

The single "fetch everything" entrypoint — one call fetches every
configured playlist across every music source *and* every podcast show
for a profile, instead of invoking `fetcher-apple`/`fetcher-ytmusic`/
`podcast-manager` separately per playlist. Root workspace member;
depends on `common`, `fetcher-apple`, `fetcher-ytmusic`, and
`podcast-manager` directly (imports their `download`/`api` functions —
doesn't subprocess out to their own CLIs).

## Usage

```bash
uv run music-stack fetch --profile config/profiles/<you>.yaml
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
- `--show` (repeatable) — restrict podcast fetch to specific show(s), by
  UUID or case-insensitive title.
- `--storefront` (default `us`) — Apple Music storefront.
- `--lock-timeout` (default 1800)

Per-item failures (one bad playlist, one unreachable show) are caught
and reported, not raised — the rest of the run continues. Unmatched
`--playlist`/`--show` names print a `WARNING`, not a hard failure
(likely a typo, not necessarily fatal).

**Removing a playlist from a profile's `playlists:` list requires
running this command, not just `sync-orchestrator sync`.**
This command owns `library/playlists/{profile}/` — it's the only thing
that prunes a playlist's stale `.m3u8` file once it's no longer
configured (`prune_removed_playlists` in `common/playlist.py`, same
pattern `podcast_manager.download.prune_unsubscribed_shows` uses for
podcast shows). `sync-orchestrator sync` only ever writes whatever
`.m3u8` files physically exist under that folder to the device — it has
no way to know a playlist file is *supposed* to be gone, so a leftover
stale file gets silently re-synced forever until this command deletes
it. Prints `[music] Pruned N stale playlist file(s)...` when it happens.
Only runs when a music source (`apple_music`/`ytmusic`) is active this
call — a `--playlist`-narrowed run never prunes anything outside its own
narrowed scope (same reasoning as podcast show pruning: narrowing is a
per-run choice, not "I removed this from my profile"). Use
`sync-orchestrator full-sync` (fetch + device sync in one command — see
`services/sync-orchestrator/README.md`) to get both steps without
remembering to run this one first.

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

- **`fetch-scheduler`** imports `run_fetch` directly (in-process) to
  drive its own cron-scheduled ticks — see
  `services/fetch-scheduler/README.md`.
- **`sync-orchestrator auto-sync`** (opportunistic pre-fetch step) and
  **`sync-orchestrator full-sync`** (its whole fetch stage) both shell
  out to `music-stack fetch` as a **subprocess** (deliberately, not an
  import — keeps `sync-orchestrator`'s own `iopenpod`/PyQt6 dependency
  tree from merging with this one). See
  `services/sync-orchestrator/README.md`.
