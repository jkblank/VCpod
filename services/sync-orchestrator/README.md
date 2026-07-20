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
useful for a personal library that predates this project and isn't part
of the managed config.

The device is matched against the profile's `device.match_by`/
`match_value` (`volume_label` or `serial`) — see
`services/common/src/common/models.py`'s `DeviceMatch`.

Execution is hard-gated: a plan that unexpectedly proposes removing any
existing track is refused rather than trusted, and a full device backup
(`BackupManager.create_backup`) runs before every write unless
`--skip-backup` is passed.
