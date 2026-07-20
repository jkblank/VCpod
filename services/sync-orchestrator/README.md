# sync-orchestrator

Runs **bare metal** (or a privileged container with `--device` passthrough),
not through `docker-compose.yml`, because it needs the connected iPod
visible as a mounted USB block device — see `music-stack-planning.md`
§2/§6.

Drives [iOpenPod](https://github.com/TheRealSavi/iOpenPod) headlessly (as
a library, not through its GUI) to sync music, playlists, and podcasts
onto a real click-wheel iPod. Background and every workaround this
depends on is written up in full in
[`docs/m6-ipod-headless-recommendation.md`](../../docs/m6-ipod-headless-recommendation.md)
and `notes.md`.

Standalone `uv` project (not part of the root workspace) — `iopenpod`
pulls in PyQt6, a heavy dependency kept isolated from the other services,
same reasoning as `services/fetcher-spotify`.

## Usage

Assumes the target iPod is already connected and mounted (auto-mounted by
the desktop environment) — detecting a new connection and mounting it is
M9's job ("automation"), not this one.

```bash
cd services/sync-orchestrator
uv sync

# Plan only — computes and prints the plan, writes nothing.
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state

# Review the plan output, especially to_remove, then actually write it:
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state \
    --execute
```

`--library-root`/`--state-root` are real host paths, not global.yaml's
`paths.library_root`/`paths.state_root` — those are Docker-container
paths (`/data/library`, `/data/state`) that don't exist on the bare-metal
host this service always runs on. Same explicit-path pattern already used
by `fetcher-apple`/`podcast-manager`, not a new convention.

`--pc-folder PATH` (repeatable) adds extra folders to mirror onto the
device beyond `library_root/music` and the profile's playlists folder —
useful for an ad hoc folder that isn't part of the managed config.

The device is matched against the profile's `device.match_by`/
`match_value` (`volume_label` or `serial`) — see
`services/common/src/common/models.py`'s `DeviceMatch`.

Every stage (backup, PC-side scan, fingerprinting, file writes) prints
progress as it happens, e.g. `[scan] 3120/4416 — Talking Heads/...m4a` —
these runs took 20-50+ minutes silent before this was wired up, which
made it impossible to tell "still working" from "hung." Throttled to at
most one line per second per stage (iopenpod's own progress callbacks
fire once per file, completely unthrottled) plus always on a stage
change or completion — see `sync.py`'s `_ThrottledProgressPrinter`.

### Selective sync from an external library

A profile's optional `external_library` block (see `config/profiles/
alice.yaml`/`bob.yaml` for examples) syncs a chosen subset — specific
artists/albums/tracks — of a personal library that predates this project
(e.g. `~/Music/MusicLibrary`), instead of the whole thing:

```yaml
external_library:
  path: /home/alice/Music/MusicLibrary
  mode: include   # or "exclude"
  selections:
    - "Linkin Park"                       # whole artist
    - "Fleetwood Mac/Rumours"              # whole album
    - "David Bowie/Hunky Dory/05 Life on Mars_.m4a"  # single track
    - "Talking Heads":                    # nested shorthand: several
        - "Performance"                   # album/track entries under
        - "Remixed"                       # the same artist, without
        - "The Collection"                # repeating "Talking Heads/"
```

A `selections` entry can be a plain string (matched by prefix, as above)
or a single-key mapping of artist -> list of album/track names relative
to that artist — shorthand for several entries that all start with the
same `"Artist/"` prefix. `"Talking Heads": ["Performance", "Remixed"]`
is exactly equivalent to `["Talking Heads/Performance",
"Talking Heads/Remixed"]`; the two forms can be freely mixed in the same
list, and everything is flattened to plain strings at config-load time
(`common/models.py`'s `ExternalLibraryConfig`) — `selection.py` never
sees the nested form.

`mode: include` is a whitelist (only `selections` gets synced); `mode:
exclude` is a blacklist (everything under `path` gets synced except
`selections`). Each run resolves the selection and rebuilds a staging
directory of symlinks at `state_root/.external_library_staging/{profile}`
pointing back at the real files, and mirrors *that* onto the device
instead of `path` directly — this is deliberate, not just an
implementation detail: iopenpod's `EngineOptions.allowed_paths` looks
like the natural way to scope a sync to a subset, but it narrows what
counts as "seen" during planning, and iopenpod's removal logic treats
anything previously synced but not "seen" this run as deleted from the
PC and stages it for device removal — using it directly would risk
proposing to delete previously-synced tracks that are still on disk but
just outside the new scan. Building our own staging directory sidesteps
that entirely: iopenpod only ever sees the current selection. Full
writeup in `notes.md`.

**Narrowing a selection removes tracks from the device — this is
intentional, not a bug.** The first sync after adding or tightening an
`external_library` selection will propose removing every previously
mirrored-wholesale track that's now out of scope. Review `to_remove` in
the printed plan before executing.

Execution is hard-gated: a plan proposing to remove any existing track is
refused unless `--allow-removals` is passed alongside `--execute` (both
required together — `--execute` alone still refuses on any removal,
`--allow-removals` alone does nothing). A full device backup
(`BackupManager.create_backup`) runs before every write unless
`--skip-backup` is passed.
