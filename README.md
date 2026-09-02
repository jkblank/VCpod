# VCpod (Vibe-Coded pod)

A personal "*arr-stack for music" — acquires music and podcasts from
streaming sources you already subscribe to, organizes and tags it, and
syncs it onto a real click-wheel iPod. No streaming/serving component: this
is a pipeline that ends at a physical device, not a Navidrome/Jellyfin
alternative.

Built almost entirely through AI-assisted ("vibe-coded") pair programming
with Claude Code — hence the name.

## What it does

1. **Acquires** music from streaming sources (Apple Music, Spotify, YouTube
   Music) based on config-defined playlists.
2. **Organizes and tags** the acquired audio, deduplicates across sources,
   and writes `.m3u8` playlists.
3. **Syncs podcasts**, using [Pocket Casts](https://pocketcasts.com) as the
   source of truth for subscriptions and played/unplayed state.
4. **Syncs everything onto a real iPod** — music, playlists, and podcasts —
   using [iOpenPod](https://github.com/TheRealSavi/iOpenPod) as a headless
   library, no GUI required.

Everything is driven by plain YAML config files and supports multiple
user/iPod profiles. See [`music-stack-planning.md`](music-stack-planning.md)
for the full architecture and milestone plan, and [`notes.md`](notes.md) for
a running log of real bugs found (and fixed) in both this project and the
upstream tools it depends on.

It can run entirely hands-off once set up: a scheduler keeps `library/`
fresh on each playlist's/show's own cron schedule, and plugging in the
iPod triggers a real device sync automatically (udev → a systemd
service) — see "Running it" below.

## Status

| Milestone | What | Status |
|---|---|---|
| M1 | Repo scaffold, config loader/validator | Done |
| M2 | Apple Music fetcher (`gamdl` wrapper) | Done |
| M3 | Spotify + YouTube Music fetchers | YouTube Music: done, downloads work end to end (needed a companion PO-token service for yt-dlp — see [`services/fetcher-ytmusic/README.md`](services/fetcher-ytmusic/README.md)). Spotify: built and auth-working, but downloads are blocked on a Spotify Premium requirement for API access outside this project's control (see [`services/fetcher-spotify/README.md`](services/fetcher-spotify/README.md)) |
| M4 | Library manager: cross-source dedup, playlist writer | Done |
| M5 | Podcast manager: Pocket Casts client, episode downloader | Done |
| M6 | iOpenPod headless spike: full real sync (music + playlists + podcasts) | Done — see [`docs/m6-ipod-headless-recommendation.md`](docs/m6-ipod-headless-recommendation.md) |
| M7 | Sync orchestrator core (`services/sync-orchestrator`) | Done — real device discovery + profile-driven sync plan, live-verified |
| M8 | Play-status round trip | Device-read-back path (`playstate.py`) fixed and live-verified 2026-08-02 — matched on the wrong path shape (full path vs. iopenpod's bare-filename mapping hint) and had never actually resolved a device play back to an episode; now matches by filename (see `notes.md`). Pocket-Casts-remote path (episodes marked played in the Pocket Casts app) was already working independently. Resume-position sync doesn't work via the simple write endpoint used (see `notes.md`); Pocket Casts' real app likely needs its protobuf-based sync protocol for that, not yet built |
| M9 | Automation: scheduled fetch, udev-triggered device sync, multi-profile matching | Done, live-verified — [`services/fetch-scheduler/README.md`](services/fetch-scheduler/README.md) (fetch scheduling, plus automatic library dedup/cleanup and device backup retention) and [`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md) (`auto-sync` + udev/systemd) |
| M10 | Hardening: secrets review, auth-expiry/API-failure alerting, docs | Secrets handling reviewed (consistent gitignore convention across every credential type); alerting not built yet (`fetch-scheduler`'s per-tick errors are logged, nothing pages anyone); docs — this pass |
| M11–M14 | Web GUI (backend, profiles/playlists, podcasts/sources, sync visibility) | Not started |
| M15 | Audiobooks via Libby/OverDrive | Acquisition is manual (Libby's automated auth paths are confirmed dead upstream, see `notes.md`) — but the merge/tag/sync pipeline from manually-downloaded MP3 parts to a real device is done, see [`services/audiobook-manager/README.md`](services/audiobook-manager/README.md) |

## Known issues

- **Album art could fail to render on iPod Classic-family devices (6th/7th
  gen)** — two separate, real causes, both now fixed (every byte written
  was independently verified correct in both cases; neither was a
  data-correctness bug):
  1. **Fixed**: specific tracks with Photoshop-processed
     (restart-interval) JPEG cover art reliably broke rendering whenever
     they were on the device, independent of total library size —
     `library-manager normalize-artwork`, merged, re-encodes affected
     tracks' embedded art unconditionally (no cheap way was found to
     predict which files are actually affected).
  2. **Fixed**: what looked like a real total `ArtworkDB` byte-size
     ceiling (working at 1,547 tracks/340.5MB, failing at
     1,958 tracks/394.8MB) turned out to be iopenpod's own internal
     32MB-per-`.ithmb`-file chunking, not a device/firmware limit — real
     Apple iTunes was observed writing a single 335MB `.ithmb` with no
     chunking and rendering fine. `sync_orchestrator/sync.py` now raises
     iopenpod's `ITHMB_MAX_SIZE_BYTES` to FAT32's real per-file limit
     (4GiB−1) at import time; verified on real hardware at 1,984 tracks.
  See
  [`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#known-limitations-album-art-on-ipod-classic-family-devices)
  and `notes.md` for the full investigation. `profile.music` scoping and
  Rockbox mode (`sync.mode: rockbox`, which sidesteps `ArtworkDB`
  entirely) remain useful for other reasons but are no longer required
  as workarounds for this — see
  [`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#rockbox-mode).
- See the Status table above for the other known-incomplete pieces
  (Spotify downloads blocked on a Premium API requirement, podcast
  resume-position sync, alerting).

## Setup

A step-by-step path from a fresh clone to your first real device sync.
Every source (Apple Music, YouTube Music, podcasts) is independently
optional — skip whichever credential steps don't apply to you.

### 0. Prerequisites

- [`uv`](https://docs.astral.sh/uv/) — all Python tooling runs through
  it, nothing touches your system Python.
- `ffmpeg` — used by the fetchers (cover-art handling) and
  `audiobook-manager`.
- `chromaprint` (provides `fpcalc` on `PATH`) — used by
  `sync-orchestrator` for audio fingerprinting during a device sync.
- A real click-wheel iPod (6th/7th-gen iPod Classic or 5.5th-gen iPod
  Video) if you intend to sync a device — everything up through fetching
  works fine without one connected.
- Only if you plan to use YouTube Music: [`deno`](https://deno.com/)
  (needed by both `yt-dlp` and the PO-token companion service — see
  step 3).

Arch example: `sudo pacman -S uv ffmpeg chromaprint deno` (drop `deno`
if you're skipping YouTube Music). Package names are equivalent on
other distros/Homebrew.

### 1. Install and verify

```bash
git clone <this repo's URL>
cd music-stack
uv sync
uv run pytest   # root workspace tests, should all pass
```

`services/fetcher-spotify` and `services/sync-orchestrator` are
separate, standalone `uv` projects (see "Running it" below) — their
tests run with their own `uv run pytest`, inside their own directories,
not picked up by the command above.

### 2. Pick which sources you're using

Edit `config/global.yaml`'s `sources.*.enabled` flags — turn off
whichever of Apple Music / Spotify / YouTube Music you don't plan to
use. **Spotify is currently blocked** on a Premium API requirement
outside this project's control (see the Status table above); enabling
it won't actually download anything today. Podcasts aren't gated here —
each profile that wants them just sets its own Pocket Casts credentials
(step 3) and `podcasts:` block (step 4).

### 3. Get credentials for each source you're using

All credential files are gitignored — only `config/global.yaml` and the
example profiles are meant to be committed.

**Apple Music**: export cookies from a real, logged-in
`music.apple.com` browser session (a browser extension like "Get
cookies.txt" works, Netscape format) and save them to
`config/secrets/apple_music_cookies.txt` (the path `global.yaml`
already points at). These expire every few weeks — a fetch that
suddenly fails with `GamdlApiResponseError: Error fetching account
info` almost always means it's time to re-export, not a code problem.
See `services/fetcher-apple/README.md` for a distinct, non-cookie
failure mode you might also hit.

**YouTube Music**:
1. Export YouTube cookies the same way (browser extension, Netscape
   format) to `config/secrets/youtube_cookies.txt`.
2. Optional — only needed to list your own private playlists (public
   playlists resolve fine without it): `uv run ytmusicapi oauth`,
   follow the prompts, save the result as
   `config/secrets/ytmusic_oauth.json`.
3. **Required for any real download**: set up and run the
   `bgutil-ytdlp-pot-provider` companion service — YouTube's bot-check
   blocks every real audio format without it. Full clone/build steps in
   `services/fetcher-ytmusic/README.md`; it needs to stay running for
   the whole time you're fetching.

**Podcasts (Pocket Casts)**: create
`config/secrets/pocketcasts/<you>.json`:
```json
{"email": "you@example.com", "password": "your-pocketcasts-password"}
```

**Spotify**: shelved — see step 2, nothing to configure until the
upstream Premium API requirement is resolved.

### 4. Create your profile

```bash
cp config/profiles/alice.yaml config/profiles/<you>.yaml
```

Edit it — `alice.yaml`/`bob.yaml` document every field inline as
comments, so treat them as the reference. At minimum you'll set:
- `device.match_by`/`match_value` — see step 6 for how to find these
  once your iPod is connected.
- `playlists:` — one entry per playlist you want fetched, per source.
- `podcasts.pocketcasts.credentials_file` — pointing at the file from
  step 3.
- `sync:` — transcode format, whether to push played state back to
  Pocket Casts, etc.

```
config/
├── global.yaml                    # shared source enable flags, credential paths
├── profiles/
│   ├── alice.yaml, bob.yaml        # example profiles — copy one to get started
│   └── <you>.yaml                  # your real profile — gitignored, never commit this
└── secrets/                        # real credentials — gitignored entirely
```

Real per-user profiles and everything under `config/secrets/` are
gitignored — only the example profiles are meant to be committed.

### 5. First fetch

```bash
uv run music-stack sync --profile config/profiles/<you>.yaml
```

Downloads every playlist across every enabled source, plus podcasts,
into `library/`. Safe to re-run any time — already-downloaded
tracks/episodes are skipped, not re-fetched.

### 6. First device sync

Connect your iPod. If you don't already know its `device.match_value`
for step 4, the simplest option is `match_by: volume_label` — find it
with:

```bash
lsblk -o NAME,LABEL,FSTYPE,MOUNTPOINT
```

(look for the `vfat`/`hfsplus` partition with your iPod's name). Fill
that label into your profile's `device.match_value`, then plan-only
first and review the plan — especially `to_remove` — before writing
anything real:

```bash
cd services/sync-orchestrator
uv sync
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state
# review the plan, then:
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state \
    --execute
```

Removals need an extra `--allow-removals` flag on top of `--execute` —
see `services/sync-orchestrator/README.md` for the full flag reference,
matching by serial instead of volume label, and the one-command
`full-sync` (fetch + device sync together).

### 7. Automate it (optional)

Once the above works end to end by hand, see "Running it" below for
scheduled unattended fetching and fully-automatic sync-on-connect —
neither is required, both build on exactly the commands above.

### Running it

Three ways to run this, roughly in order of "how hands-off do you want
it to be." All examples assume you're at the repo root with a real
profile at `config/profiles/<you>.yaml` (see Setup above).

**1. One-shot manual sync** — fetches every playlist across every
source, plus podcasts, for a profile in one call:

```bash
uv run music-stack sync --profile config/profiles/<you>.yaml
```

Full flag reference: [`services/music-stack-cli/README.md`](services/music-stack-cli/README.md).

**2. Scheduled, unattended fetching** — runs continuously (or via
cron/systemd timer with `--once`), fetching whatever's due per each
playlist's/show's own `fetch_schedule` (a cron expression in profile
config), independent of whether any device is connected. Also runs
library dedup/cleanup and device backup retention automatically, if
enabled:

```bash
uv run --project services/fetch-scheduler fetch-scheduler --config-root config
```

Full details, config schema, and the maintenance-task flags:
[`services/fetch-scheduler/README.md`](services/fetch-scheduler/README.md).

**3. Fully automatic device sync on connect** — plugging the iPod in
triggers a real sync with no manual command at all, via a udev rule →
systemd service. Requires a one-time manual install (`sudo`, touches
system udev/systemd config — deliberately not automated):
[`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#automation-m9-auto-sync--udev).

**Audiobooks** — manual, one-off per book (no automated acquisition
exists, see M15 above): merge a folder of MP3 parts into one tagged,
chaptered `.m4b`, then sync it like anything else via `--pc-folder`:

```bash
uv run audiobook-manager import-audiobook \
    --parts-dir "path/to/Author - Title" \
    --library-root library/audiobooks --state-root state
```

Full details: [`services/audiobook-manager/README.md`](services/audiobook-manager/README.md).

---

Each of the above composes smaller, independently-usable services — see
their own READMEs for manual/advanced usage (single-playlist fetches,
listing an account's playlists, pushing podcast play-state back to
Pocket Casts, running dedup on demand, plan-only device syncs, etc.):

| Service | What |
|---|---|
| [`common`](services/common/README.md) | Shared config schema, state db, scheduling/backup-retention logic every other service builds on |
| [`fetcher-apple`](services/fetcher-apple/README.md) | Apple Music playlist downloader (`gamdl`) |
| [`fetcher-ytmusic`](services/fetcher-ytmusic/README.md) | YouTube Music playlist downloader (`ytmusicapi` + `yt-dlp`) — needs a companion PO-token service |
| [`fetcher-spotify`](services/fetcher-spotify/README.md) | Spotify playlist downloader (`zotify`) — shelved, blocked on a Premium requirement |
| [`library-manager`](services/library-manager/README.md) | Cross-source dedup + quarantine cleanup |
| [`podcast-manager`](services/podcast-manager/README.md) | Pocket Casts client, episode downloader, play-state push-back |
| [`music-stack-cli`](services/music-stack-cli/README.md) | The unified `music-stack sync` command |
| [`fetch-scheduler`](services/fetch-scheduler/README.md) | Cron-scheduled fetching + automatic library/backup maintenance |
| [`sync-orchestrator`](services/sync-orchestrator/README.md) | Device sync engine (bare metal) + `auto-sync`/udev automation + `full-sync` (interactive fetch+device in one command) |
| [`audiobook-manager`](services/audiobook-manager/README.md) | Merges manually-acquired MP3 parts into a tagged, chaptered `.m4b` (ffmpeg + beets-audible) |

Device sync (`sync-orchestrator`) needs the iPod connected/mounted and
must run on bare metal, not through Docker (see Architecture below) —
plan-only first, review the plan (especially `to_remove`), then
`--execute`:

```bash
cd services/sync-orchestrator
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state
# review the plan, then:
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state \
    --execute
```

For fetch + device sync in one interactive command instead (bare
profile name, no other paths needed) see `full-sync` in
[`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#one-command-fetch--device-full-sync):

```bash
uv run sync-orchestrator full-sync --profile <you> --config-root ../../config
```

### Running with Docker

`fetch-scheduler` is the one long-running Compose service — `restart:
unless-stopped`, no profile gate, always included:

```bash
docker compose up -d fetch-scheduler
```

That's the containerized equivalent of "Running it" option 2 above
(scheduled fetching + automatic maintenance) — it reads the same
`config/global.yaml`/`config/profiles/*.yaml` and bind-mounts the same
`library/`/`state/` directories a bare-metal run would use.

Individual fetcher containers are one-shot and gated behind Compose
profiles instead, one per music source (`apple`, `spotify`, `ytmusic`),
matching `global.yaml`'s `sources.*.enabled` flags — useful for a manual
run without invoking `uv` directly. `library-manager` and
`podcast-manager` have no profile and always run when invoked.

```bash
docker compose --profile apple up
docker compose --profile apple --profile spotify up   # multiple sources
```

`ytmusic` needs one more thing Compose doesn't manage: the
`bgutil-ytdlp-pot-provider` companion service must be running and
reachable before `fetcher-ytmusic` can actually download anything (not
just an optional nicety — every download fails without it). See
`services/fetcher-ytmusic/README.md` for setup.

`audiobook-manager` is also gated behind its own profile, but for a
different reason than the fetchers — it's a manual, one-book-at-a-time
CLI tool (not tied to any `global.yaml` flag), and it pulls in beets'
real dependency weight (`numpy`/`scipy`/`numba`/`llvmlite`), so it isn't
built by default:

```bash
docker compose --profile audiobooks run --rm audiobook-manager \
    import-audiobook --parts-dir /data/library/... \
    --library-root /data/library/audiobooks --state-root /data/state
```

Or set `COMPOSE_PROFILES` in `.env` once instead of passing `--profile`
every time (see `.env.example`). Compose doesn't read `global.yaml`
itself, so keep the two in sync by hand — enabling a source there
without also enabling its profile here just means that fetcher's
container never runs.

## Architecture

- **Docker vs. bare metal split**: acquisition/processing services
  (fetchers, library-manager, podcast-manager, fetch-scheduler) only read
  config and write to shared volumes, so they containerize cleanly. The
  iPod sync step (`sync-orchestrator`) needs real USB device access and
  runs on bare metal — udev + a systemd service trigger it automatically
  on connect (see `services/sync-orchestrator/README.md`), rather than
  trying to replicate udev hotplug handling inside a container (would
  need `--privileged` + host `/dev` sharing, mostly erasing the point of
  containerizing it — see `notes.md`).
- **Config is the only source of truth** — no database of settings, no
  hidden state beyond what's in `config/` and the per-profile `state/*.sqlite`
  (source-ID-to-local-file maps and sync history, not configuration).
- **iOpenPod as a library, not a GUI dependency** — the sync step drives
  iOpenPod's real sync engine (`SyncEngine`, `BackupManager`,
  `itunesdb_parser`/`itunesdb_writer`) directly, headlessly. See the M6
  recommendation doc for the full investigation.

## A note on the fetchers

`gamdl` (Apple Music), the Spotify fetcher, and the YouTube Music fetcher
(`yt-dlp` + a PO-token companion service to get past YouTube's bot-check)
all operate in a legal/ToS gray area — they're personal-use tools for
downloading music you already have access to via your own paid
subscription, not intended for redistribution or exposure as a public
service. Use accordingly.

## Non-goals

- No streaming/serving of music — this is not a Navidrome/Jellyfin
  alternative.
- No iPod Touch / iOS device support — click-wheel iPods only.
- No in-browser playback or user-account system in the (future) web GUI.

## Acknowledgments

This project exists because of the real, hard reverse-engineering and
protocol work done by others. In particular:

- [**gamdl**](https://github.com/glomatico/gamdl) by
  [glomatico](https://github.com/glomatico) — the Apple Music
  downloader `fetcher-apple` wraps.
- [**iOpenPod**](https://github.com/TheRealSavi/iOpenPod) by
  [John Gibbons](https://github.com/TheRealSavi) — the click-wheel iPod
  sync engine `sync-orchestrator` drives headlessly. See
  [`docs/m6-ipod-headless-recommendation.md`](docs/m6-ipod-headless-recommendation.md)
  for how deep this project actually goes.
- [**zotify**](https://github.com/zotify-dev/zotify) and its actively
  maintained fork, [**Googolplexed0/zotify**](https://github.com/Googolplexed0/zotify) —
  the Spotify fetcher this project migrated to (currently shelved on a
  Spotify Premium API requirement, not a code issue — see `notes.md`).

None of these projects are affiliated with or endorse this one.

## License

MIT — see [`LICENSE`](LICENSE).
