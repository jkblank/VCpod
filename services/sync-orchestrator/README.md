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

## Known limitations: album art on iPod Classic-family devices (both fixed)

Two separate, real causes on iPod Classic-family devices (6th/7th gen —
the 5.5th-gen iPod Video is unaffected, see `docs/iopenpod-artworkdb-
missing-mhii-chunk.md`), confirmed independent of each other by
real-device testing (2026-08-24/25/26, see `notes.md` for the full
investigation) — every byte written for both was independently verified
correct (right pixels, right format, right `iTunesDB`↔`ArtworkDB`
cross-references) in both cases; neither was a data-correctness bug in
`sync-orchestrator`. Both are now fixed.

### 1. Specific tracks with Photoshop-processed cover art (fixed)

At least one real track ("Cool Kids" by Echosmith) whose embedded JPEG
cover art carries a DRI (Define Restart Interval) marker — consistent
with having been processed through Photoshop or similar tooling —
reliably broke rendering whenever it was on the device, confirmed
independent of total track count/size (same exact track count and
`ArtworkDB` byte size landed on both sides of "working" depending only
on whether this track's art was included).

**Fixed**: `library-manager normalize-artwork` re-encodes embedded
cover art through Pillow (which never writes the flagged markers) for
any affected track, unconditionally rather than trying to predict which
files are actually broken (the marker alone isn't a usable predictor —
94.5% of the real library carries it, most rendering fine) — see
`services/library-manager/README.md`'s "Artwork normalization" section.
Verified via a full clean-wipe real-device sync before merging.

### 2. `.ithmb` file chunking broke rendering past ~1,500-1,800 tracks (fixed)

Independent of fix #1 above: what looked like a real total `ArtworkDB`
byte-size ceiling on the one 6th Gen/80GB test device measured —
confirmed **working at 1,547 tracks/340.5MB**, confirmed **failing at
1,958 tracks/394.8MB**, both via full clean-wipe syncs with fix #1
already applied either way — turned out not to be about total bytes at
all. iopenpod's own `artworkdb_writer.py` rolls each artwork format
(`F1055`/`F1060`/`F1061`) over to a new `F{format}_N.ithmb` file once
the current one passes 32MB (`ITHMB_MAX_SIZE_BYTES`) — a self-imposed
budget unrelated to any real device/filesystem limit (iopenpod tracks
the actual FAT32 per-file ceiling separately, in
`device/filesystem_profile.py`, but never threads it into the artwork
writer).

Confirmed via a real comparison point (2026-08-26): linking the whole
real library to genuine Apple iTunes and syncing it onto the same test
device produced a single 335MB `F1060_1.ithmb`, no rollover — and it
rendered fine. Parsing that `ArtworkDB` with iopenpod's own
`read_existing_artwork()` showed 434MB of *live* artwork data working
correctly, well past the 394.8MB point where iopenpod's own
32MB-chunked write fails — directly ruling out total bytes as the
limiting factor. It's the multi-file split itself that breaks
rendering.

**Fixed**: `sync_orchestrator/sync.py` overrides iopenpod's
`ITHMB_MAX_SIZE_BYTES` to FAT32's real per-file limit (`4 * 1024**3 -
1`, the same number iopenpod's own `filesystem_profile.py` already
uses elsewhere) at import time, since `iopenpod` is a plain pinned PyPI
dependency here, not vendored — this is a runtime monkeypatch, not an
upstream fix, and is worth upstreaming properly at some point. Verified
on real hardware: full clean-wipe sync of the entire real library
(1,984 tracks, single un-chunked `.ithmb` per format) — album art
renders.

`profile.music` scoping and Rockbox mode (below) remain useful for
their own reasons (device-specific curation, Rockbox users) but are no
longer required as workarounds for either issue above.

## Rockbox mode

Set `sync.mode: rockbox` on a profile (default is `itunes`, i.e. every
profile's existing behavior, unchanged) to sync a device running
[Rockbox](https://www.rockbox.org/) instead of stock iPod firmware.
Rockbox ignores `iTunesDB`/`ArtworkDB` entirely and reads metadata/art
straight from each file's own tags, so this mode is a genuinely separate
code path (`rockbox_sync.py`), not a flag on the existing one:

- **No iTunesDB, no ArtworkDB, no iopenpod `SyncEngine`** — a plain
  filesystem mirror instead: `library/music` (the shared pool, same
  "playlists curate, don't scope" design as iTunes mode — see
  `notes.md`) mirrored to `Music/` on the device, `external_library`/
  `audiobooks`/selected podcast episodes to their own top-level folders,
  transcoded per `sync.transcode_format` via iopenpod's standalone
  transcoder functions (no `SyncEngine` needed for that part either).
- **Real, physical playlist files** under `Playlists/`, with paths
  relative to the playlist file itself (the portable M3U convention) —
  deliberately not iopenpod's own `rockbox_metadata_support` option
  (`EngineOptions.rockbox_metadata_support`), which still writes a full
  iTunesDB (still exposed to the album-art ceiling above) and — more
  importantly — never places a physical `.m3u8` on the device at all:
  iopenpod's playlist-file handling parses `.m3u8` files into native
  iTunesDB playlist objects at scan time and only ever consumes them,
  never copies them. Rockbox would see no playlists under that option.
- **Change detection** compares each file's mtime against the source
  file's mtime (FAT32's 2-second resolution tolerated) rather than
  content hashing, propagated onto the device copy at write time.
- **Cover art**: ffmpeg's audio-only transcode commands strip embedded
  art (`-vn`) even though basic tags survive — confirmed by reading
  iopenpod's own `transcoder.py`. A plain copy (source already in a
  native, no-transcode-needed format) preserves art byte-for-byte; any
  track that actually goes through ffmpeg gets its original art
  re-embedded afterward from the source file directly (mutagen, ID3 or
  MP4 atoms depending on the output format).
- **Known gaps (v1)**: `push_play_status_back` has no effect in this mode
  (Rockbox keeps its own play history in `.rockbox/`, unreadable by this
  project today — explicit no-op, logged at debug, not silent). Device
  recognition (`is_ipod_mount`) accepts a `.rockbox/` directory as well
  as the usual `iPod_Control/Device/SysInfo`, but this hasn't been
  verified against a real Rockbox-loaded device yet.

Same commands, same flags (`sync`/`full-sync`/`auto-sync`,
`--execute`/`--allow-removals`/etc. all mean the same thing) — the mode
lives on the profile, not the CLI invocation.

## Usage

Assumes the target iPod is already connected. If it isn't mounted yet
(no desktop auto-mount daemon in the session, or a testbed device that
was just unplugged/replugged), `sync`/`full-sync`/`auto-sync` all
best-effort auto-mount every unmounted vfat/hfsplus partition first via
`mount_candidate_devices()` before scanning for a match — see the
auto-sync section below for the mechanism.

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
device beyond `library_root/music`, the profile's playlists folder, and
`library_root/audiobooks` (see below) — useful for an ad hoc folder
that isn't part of the managed config.

**`library_root/music` is always included, for every profile — a
profile's own `playlists:` list controls what gets *fetched*, not what
ends up in the device's Music/Songs library.** This is standard
iTunes-style "entire library" device sync behavior, by design, not a
bug: `playlists:` scopes actual on-device *playlists* (via
`library_root/playlists/{profile}/`), which are curation views into
that one shared pool, not a content filter on it. A profile with a
short playlist list still gets the whole shared library synced to its
device's general browse view. If a device genuinely needs to be scoped
to less than the full shared library, that requires an isolated
`--library-root` pointed at a separate directory, not a shorter
`playlists:` list.

The device is matched against the profile's `device.match_by`/
`match_value` (`volume_label` or `serial`) — see
`services/common/src/common/models.py`'s `DeviceMatch`.

### Music library scope

`library_root/music` is one shared pool across every profile by design —
a profile's `playlists:` list only controls what gets *fetched* and what
shows up as a curated on-device *playlist*, not what lands in the
device's general Music library (standard iTunes-style "entire library"
sync behavior). Most profiles want the whole pool (leave `music:` unset
— every profile's behavior before this option existed, unchanged).

For a profile that wants a genuinely small, curated device instead —
e.g. one meant to carry only specific playlists, nothing else — set
`music:` (`ProfileConfig.music`, `MusicLibraryConfig` in
`common/models.py`), same include/exclude + `selections` shape as
`audiobooks`/`external_library`, matched by `{Artist}` /
`{Artist}/{Album}` path-fragment prefixes:

```yaml
music:
  mode: include
  selections: []   # empty = nothing extra beyond this profile's own playlists
```

Unlike `audiobooks` (empty selections = everything), `music:`'s empty
default is the opposite — a profile that sets `music:` at all is opting
into curation, so "curate down to nothing" is the sensible empty case
here, same convention `external_library` already uses.

**A profile's own playlist tracks are always included regardless of this
scoping** — there's no way around that, by design: on a real iPod, the
on-device Songs list and its playlists share one flat track table (a
track can't be "playlist-only" in iTunesDB), and iopenpod's own
playlist-file discovery resolves each `.m3u8` entry directly off disk,
unconstrained by `pc_folders` — so removing `library/music` from
`pc_folders` never drops a playlist's own tracks in iTunes mode.
Rockbox mode (`rockbox_sync.py`) makes the same guarantee itself, since
it doesn't get it for free the way iTunes mode does. In other words:
`music: {selections: []}` on a profile with playlists configured means
"exactly this profile's playlist tracks, nothing else" — which is
exactly what fixed the original problem this option was built for (a
device ending up with every track ever fetched by any profile, most of
them not on any playlist the device's owner actually wanted).

### Audiobooks

`library_root/audiobooks` (populated by
[`audiobook-manager`](../audiobook-manager/README.md)) syncs
automatically whenever it exists — no `--pc-folder` needed. Which
books actually go to a given profile's device is controlled by that
profile's `audiobooks:` config block (`ProfileConfig.audiobooks`,
`AudiobooksConfig` in `common/models.py`), same include/exclude +
`selections` shape as `external_library` below, matched by
`{Author}` / `{Author}/{Album}` path-fragment prefixes instead of
artist/album:

```yaml
audiobooks:
  mode: include   # default; empty selections = every audiobook syncs
  selections: []
```

Leaving the whole `audiobooks:` block out of a profile entirely behaves
identically to the default above — every audiobook syncs, no curation
needed unless you want it. A non-default selection (either mode, with a
non-empty `selections` list) is resolved into a symlink staging dir the
same way `external_library` is (see `selection.py`'s
`resolve_audiobooks_folder`) — the whole `library_root/audiobooks` tree
is only ever handed to `iopenpod` unfiltered when no real filtering is
needed, to avoid staging-dir overhead for the common case.

Every stage (backup, PC-side scan, fingerprinting, file writes) prints
progress as it happens, e.g. `[scan] 3120/4416 — Talking Heads/...m4a` —
these runs took 20-50+ minutes silent before this was wired up, which
made it impossible to tell "still working" from "hung." Throttled to at
most one line per second per stage (iopenpod's own progress callbacks
fire once per file, completely unthrottled) plus always on a stage
change or completion — see `sync.py`'s `_ThrottledProgressPrinter`.

### Podcasts

Every plan (unless `--skip-podcasts`) does three podcast-related things,
merged into the same plan/`to_remove` gate as music:

1. **Device read-back**: before planning, reads real listening progress
   off the device's own Play Counts file (`playstate.py`'s
   `resolve_played_states`, via iopenpod's `MappingManager` — read-only,
   safe on a plan-only run) and records it into `podcast-manager`'s state
   db. This is independent of Pocket Casts ever seeing that listen.
2. **Adds** newly-downloaded episodes not yet on this device
   (`_load_podcast_feeds` + iopenpod's `build_podcast_sync_plan`).
3. **Removes** any episode the state db already knows is played — merged
   from Pocket Casts and/or the device read-back in step 1, the same
   `played` flag `podcast-manager`'s own `delete_played_episodes` cleanup
   uses — that's still actually present on this device
   (`podcast_removal.py`'s `build_podcast_removal_items`, matching by
   enclosure URL / title+album like iopenpod's own matcher). This is
   deliberately keyed off the state db, not "does the local file still
   exist": `podcast-manager` typically deletes a played episode's file
   before this ever runs, so a file-presence check would miss exactly the
   episodes meant to be removed.

Podcast removals flow through the same `--allow-removals` gate as
everything else in `to_remove` — no separate flag. A "just finished
listening, then plugged in" episode gets removed in that same sync run,
since step 1's read-back runs before step 3's removal plan is built.

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

## One command, fetch + device: `full-sync`

`sync-orchestrator sync` (above) only writes to the device — it assumes
whatever's already in `library/` is what you want synced. Getting fresh
content there first means running `music-stack sync` separately, with
its own full path flags. `full-sync` does both in one command, built for
an interactive human at a terminal (as opposed to `auto-sync` below,
which is unattended-only):

```bash
# Fetches + syncs the whole profile, plan-only (no --execute passed):
uv run sync-orchestrator full-sync --profile john --config-root ../../config

# Same, but actually write the device plan:
uv run sync-orchestrator full-sync --profile john --config-root ../../config --execute

# Narrow to one playlist, fetch-only (no device stage at all) — a quick
# way to check a single playlist's fetch without touching the device:
uv run sync-orchestrator full-sync --profile john --config-root ../../config \
    --playlist "Rise Up" --fetch-only
```

- `--profile` accepts a bare profile name (resolved against
  `--config-root/profiles/{name}.yaml` via
  `common.config.resolve_profile_path`) as well as a literal path — both
  work everywhere `--profile` appears in this project, this is just the
  first command where the name form is genuinely convenient.
- `--library-root`/`--state-root` default to `library`/`state` next to
  `--config-root` when omitted — same convention `music-stack sync`
  already uses, so the common case needs no path flags at all beyond
  `--config-root`.
- `--source`/`--playlist`/`--show` (repeatable) narrow the fetch stage,
  forwarded straight to the `music-stack sync` subprocess — same
  semantics as that command's own flags.
- `--fetch-only` stops after fetching, skipping the device stage
  entirely.
- `--execute`/`--allow-removals` are still plain, separate opt-ins here
  (unlike `auto-sync`) — this command runs interactively, so the same
  "review the plan first" safety gate `sync` has applies unchanged.

## Automation (M9): `auto-sync` + udev

`sync-orchestrator sync` (above) always takes an explicit `--profile` and
requires the device to already be connected. `auto-sync` is the
unattended counterpart used by the udev rule below: given only "a device
just connected," it figures out *which* profile matches, optionally
pre-fetches, and syncs — no `--profile` flag, no `--execute`/
`--allow-removals` flags (both are always on).

```bash
uv run sync-orchestrator auto-sync \
    --config-root ../../config \
    --library-root ../../library \
    --state-root ../../state
```

1. **Auto-mounts, then matches a profile.** Each poll tick (every
   `--poll-interval` seconds, default 1, for up to `--wait-seconds`,
   default 30) first calls `mount_candidate_devices()` — best-effort
   `udisksctl mount` of every currently-unmounted vfat/hfsplus partition
   on the system — since a udev-triggered run has no guarantee any
   desktop session's auto-mount daemon is actually watching for the
   device (unlike an interactive `sync` invocation, where a human has
   typically already seen it mounted in their file manager). Individual
   mount failures (an unrelated USB drive, permissions, etc.) are
   swallowed, never block finding the real iPod. Then scans every
   `config/profiles/*.yaml` and matches by `device.match_by`/
   `match_value`, same as `sync`'s single-profile match
   (`find_matching_profile` in `device.py`). Two profiles matching the
   same connected device is treated as a config bug (duplicate/incorrect
   `device.match_value`) and fails immediately rather than picking one.
2. **Conditionally pre-fetches.** If any of the matched profile's
   playlists/podcast shows have their next scheduled fetch (see
   `fetch_schedule` in `music-stack-planning.md` §3) due within
   `--pre-fetch-horizon-hours` (default 4), invokes `music-stack sync` as
   a subprocess for just those targets before syncing to device — so
   "plug in before bed" doesn't miss data that was about to refresh
   anyway. This is deliberately a **subprocess** call
   (`--music-stack-project-dir`, default `services/music-stack-cli`), not
   an in-process import: `sync-orchestrator` is kept standalone
   specifically so its `iopenpod`/PyQt6 dependency tree never merges with
   `music-stack-cli`'s (gamdl, yt-dlp, etc.) — same reasoning as
   `fetcher-spotify`'s isolation. A failed pre-fetch only logs a warning;
   it never blocks the sync below from running against whatever's already
   in `library/`. Most connections won't have anything due this soon, so
   most runs skip this step entirely and sync immediately.
3. **Syncs, always with removals allowed.** Unlike `sync`, `auto-sync`
   hardcodes `--execute --allow-removals` — an unattended run must behave
   exactly like reviewing and confirming a removal-inclusive sync
   yourself, not a more cautious partial sync that silently leaves stale
   tracks on the device. There is no flag to opt out of this.

### Installing udev automation

`udev/99-ipod-music-stack.rules` (the trigger) + `udev/music-stack-
auto-sync.service` (the actual work, as a systemd unit) — **not
installed automatically**, since this requires root and touches system
udev/systemd config. Install them yourself:

```bash
# 1. Edit the two placeholder paths in music-stack-auto-sync.service's
#    ExecStart/StandardOutput/StandardError lines to match this
#    checkout's actual location, then:
sudo cp services/sync-orchestrator/udev/99-ipod-music-stack.rules /etc/udev/rules.d/
sudo cp services/sync-orchestrator/udev/music-stack-auto-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
```

**Why a systemd service instead of a `RUN+=` shell script** (what an
earlier version of this did): confirmed live — a `RUN+=` script, even
detached with `setsid ... &`, runs inside `systemd-udevd`'s own
per-device cgroup, which gets killed once udev considers that device's
event handling finished. It visibly started (successfully auto-mounted
the device) but vanished with zero log output before the real sync could
run. `TAG+="systemd"` + `ENV{SYSTEMD_WANTS}=...` in the `.rules` file
instead hands the device off to systemd, which starts
`music-stack-auto-sync.service` as a fully independent unit, immune to
udev's own process lifecycle.

Test without physically unplugging/replugging:

```bash
sudo udevadm trigger --action=add --attr-match=idVendor=05ac --attr-match=idProduct=1209
tail -f state/auto-sync.log
# or, via systemd directly:
systemctl status music-stack-auto-sync.service
journalctl -u music-stack-auto-sync.service -f
```

If you sync a different iPod generation/PID, add another
`ATTR{idProduct}=="..."` line to the `.rules` file (5th/5.5th gen share
`0x1209`; other generations use different PIDs not yet catalogued by this
project).
