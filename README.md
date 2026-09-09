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
for the full architecture and milestone plan, [`notes.md`](notes.md) for
a running log of real bugs found (and fixed) in both this project and the
upstream tools it depends on, and [`CHANGELOG.md`](CHANGELOG.md) for
user-facing changes — check it before pulling an update, especially any
marked **Breaking**.

It can run entirely hands-off once set up: a scheduler keeps `library/`
fresh on each playlist's/show's own cron schedule, and plugging in the
iPod triggers a real device sync automatically (udev → a systemd
service) — see "Setup" below, Docker or manual.

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
| M11 | Web GUI backend + frontend scaffold | Done — [`services/web-gui-backend`](services/web-gui-backend/README.md) (FastAPI, reads/writes profiles + global config through the shared `common/config.py` loader, validation errors surface to the caller as `{path, errors}`) + [`services/web-gui-frontend`](services/web-gui-frontend/) (React/Vite; Overview + Profiles screens working end to end against a real backend, including live device-identify — see `notes.md`) |
| M12 | Web GUI: playlist/podcast picking, credential capture | Done — Music sources picker (Apple Music/YouTube Music playlists, including adding any public YouTube playlist by share link, not just ones saved to your account), Podcasts picker (Pocket Casts subscriptions), credential capture forms (Apple Music/YouTube cookie paste, Pocket Casts login validated against a real login attempt before saving) with the big plaintext-storage warning shown above every input, cron-free schedule builder, External library + Audiobooks screens (browse a real directory tree and tick what to sync) — see `notes.md` for the full "M12a"/"M12b" build log |
| M13 | Web GUI: full podcast settings (fill_modes/episode_filter/schedule), Sources & credentials status screen, `ytmusic_oauth.json` device-code capture, one-process serving | Done — see `notes.md`'s three 2026-09-03 M13 entries for the full build log |
| M14 | Web GUI: sync visibility, auto-sync setup (generate systemd/udev files, never run them) | Done — compute/review/execute a real device sync from the browser (`/api/sync/plan`, `/api/sync/execute`, streamed live via Server-Sent Events) plus a "Dangerous mode" that skips straight to executing with removals allowed; auto-sync setup card generates the filled-in systemd unit + udev rule and the exact `sudo` commands, never runs them itself — see `notes.md` |
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

Two ways to run this — pick one:

- **Docker (recommended)**, below — one `docker compose up`, then
  configure everything from a browser. No local Python/Node toolchain
  needed, and it's the faster path to a first working setup. The one
  thing that stays outside a container either way is the actual device
  sync, for reasons explained inline below.
- **[Manual / local dev install](#manual--local-dev-install)**, further
  down — everything via `uv`/`npm` directly, no Docker at all. Slower to
  a first working setup, but no container layer between you and what's
  actually running; use this if you're developing on this codebase, or
  would just rather not use Docker.

Every source (Apple Music, YouTube Music, podcasts) is independently
optional in both paths — skip whichever credential steps don't apply to
you.

### Docker (recommended)

#### 1. Prerequisites

- [Docker](https://docs.docker.com/engine/install/) with the Compose
  plugin (`docker compose version` to check — bundled with any current
  Docker install).
- A real click-wheel iPod (6th/7th-gen iPod Classic or 5.5th-gen iPod
  Video), connected to whichever machine you'll run `docker compose`
  on — needed for step 6 (device sync), not for anything before it.
- Only if you also want fully **unattended** sync-on-connect (step 7,
  optional): `uv`, `ffmpeg`, and `chromaprint` installed on that same
  machine too. That one piece stays bare metal no matter what — see
  "Why the sync step needs privileged Docker access" below for why.

#### 2. Clone and configure the environment

```bash
git clone <this repo's URL>
cd music-stack
cp .env.example .env
```

Edit `.env`: set `COMPOSE_PROFILES` for the sources you use (`apple`,
`ytmusic`, `spotify` — see the comments in `.env.example`), and, if
this is a headless server rather than your own desktop, `HOST_MEDIA_ROOT`
(wherever this host auto-mounts removable media) and `WEB_GUI_PORT` if
`8420` is already taken.

#### 3. Bring up the stack

```bash
docker compose up -d --build web-gui-backend fetch-scheduler
```

`--build` matters here: unlike a typical self-hosted app, there's no
prebuilt image on a registry to pull — every service in this repo
builds from its own Dockerfile, so the first run takes a few minutes.
`fetch-scheduler` starts keeping `library/` fresh on each playlist's/
show's own schedule immediately; `web-gui-backend` serves both the API
and the browser UI on one port.

#### 4. Configure everything from the browser

Visit `http://<this machine>:8420/` (`http://127.0.0.1:8420/` if
running locally) — no YAML editing needed:

- **Profiles** screen — "New profile"; with the iPod already connected,
  a "Detected device" shortcut fills in `device.match_by`/`match_value`
  from a live scan instead of you looking it up by hand.
- **Sources & credentials** screen — enable whichever of Apple
  Music/YouTube Music you use and paste in their credentials.
- **Music sources** screen — tick which playlists to actually sync,
  per source.
- **Podcasts** screen — enter your Pocket Casts login (validated
  against a real login attempt before it's saved) and tick which shows
  to sync.

What to actually export/paste for each source is identical either way
you set this up — see the manual install's ["Get
credentials"](#4-get-credentials-for-each-source-youre-using) step
below for the exact, per-source instructions (cookie export, YouTube
OAuth, Pocket Casts). Everything there applies here too; only *where*
it ends up (a form field vs. a file you place yourself) differs.

Using the **External library** screen (an existing music collection
elsewhere on disk) or the Audiobooks screen's **discover** drop-zone?
Those point at a real host path used as-is, unlike everything above —
set `EXTERNAL_LIBRARY_PATH`/`AUDIOBOOK_DISCOVER_ROOT` in `.env` to that
same path first (see the comments in `.env.example`), or the browser
won't be able to see it.

#### 5. First fetch

```bash
docker compose run --rm music-stack --profile /config/profiles/<you>.yaml
```

Downloads everything the profile you just created asks for, into
`library/`. Safe to re-run any time — already-downloaded tracks/
episodes are skipped. `fetch-scheduler` (already running since step 3)
keeps doing this automatically from here on, so this manual run is
just for a first look.

#### 6. First device sync

With the iPod connected to the same machine `docker compose` is
running on, open the **Sync** screen: "Compute plan" (read-only —
review the `to_remove` list especially), then "Execute sync".

This works because `web-gui-backend`'s container runs `--privileged`
with real access to the iPod's block device — see "Why the sync step
needs privileged Docker access" below for how, and what to check if it
can't find a connected device.

#### 7. Automate it (optional)

Fetching is already automatic (`fetch-scheduler`, since step 3). For
**unattended sync-on-connect** too — plugging the iPod in triggers a
real sync with no browser involved at all — the Sync screen's "Set up
auto-sync" card generates a systemd unit + udev rule with this
install's real paths already filled in, plus the exact `sudo` commands
to install them (it never runs those commands itself). This one piece
runs bare metal on whichever machine the iPod plugs into, which is why
step 1 above flagged `uv`/`ffmpeg`/`chromaprint` as needed there for
this optional step specifically.

### Why the sync step needs privileged Docker access

`web-gui-backend`'s image vendors a full second `sync-orchestrator`
install (own venv, own `iopenpod` dependency tree — see
`services/web-gui-backend/Dockerfile`) so the Sync screen's "identify
device"/"compute plan"/"execute" buttons work by shelling out to it,
exactly like a bare-metal CLI install does. That needs real
block-device + mount access, which is why its Compose service runs
`--privileged` with `/dev`, the host's D-Bus socket, and its
removable-media mount root all bound in — see the extensive comments on
that service in `docker-compose.yml` for exactly what each mount is
for. Everything *except* this one screen runs as a normal, unprivileged
container (`fetch-scheduler` and the individual fetcher containers
never touch the device at all).

The *unattended*, udev-triggered auto-sync-on-connect (step 7 above)
is deliberately kept outside Docker entirely, rather than trying to
replicate udev hotplug handling inside a privileged container that
polls continuously — see `notes.md`'s "Distribution: why
sync-orchestrator isn't containerized too" for the full reasoning. A
button a human clicks tolerates `--privileged` fine; an always-on
background daemon was judged not worth it.

**Honestly**: the container-to-real-iPod path above (reaching a real
device through the host's udisks2/D-Bus stack from inside a container)
was built and documented from reading the actual code, not
live-verified against a running deployment — no Docker daemon was
available to build/run it in the environment this was built in. If
"identify device" doesn't find a connected iPod, `docker compose exec
web-gui-backend uv run --project services/sync-orchestrator
sync-orchestrator identify-device` is the first thing to try directly,
to see the real error past the web GUI's "not connected" degradation
(`overview.py`/`sync.py` both swallow `DeviceIdentifyError` into that
message rather than surfacing it) — most likely culprits are
`HOST_MEDIA_ROOT` not matching your distro, or
`/run/dbus/system_bus_socket` not existing at that exact path on the
host. The reliable fallback either way is step 7's bare-metal auto-sync,
which doesn't depend on any of this container-specific plumbing — that
path *is* live-verified (see `notes.md`'s 2026-09-03/2026-09-08
entries).

## Manual / local dev install

Running this directly with `uv`/`npm` instead of Docker — for local
development on this codebase, or if you'd rather not use Docker at all.
A step-by-step path from a fresh clone to your first real device sync.

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
  step 4).
- Only if you want the web GUI (step 2 — optional, everything through
  step 8 also works by hand-editing YAML with none of this installed):
  Node.js/npm, to build the frontend once.

Arch example: `sudo pacman -S uv ffmpeg chromaprint deno nodejs npm`
(drop `deno`/`nodejs npm` for whichever of YouTube Music/the web GUI
you're skipping). Package names are equivalent on other distros/Homebrew.

**Would rather use Docker?** See the ["Docker
(recommended)"](#docker-recommended) section above instead — it builds
every dependency below for you except on whichever machine the iPod
itself plugs into, since the actual device sync always runs bare metal
regardless of Docker.

### 1. Install and verify

```bash
git clone <this repo's URL>
cd music-stack
uv sync
uv run pytest   # root workspace tests, should all pass
```

`services/fetcher-spotify` and `services/sync-orchestrator` are
separate, standalone `uv` projects — their tests run with their own
`uv run pytest`, inside their own directories, not picked up by the
command above.

### 2. Start the web GUI (optional, but the easiest way to do steps 3–5)

Steps 3 through 5 below (pick sources, enter credentials, create your
profile) can all be done by hand-editing YAML, or by clicking through a
browser instead — same underlying `config/` files either way, so you
can freely mix both across steps, or come back and use the other
approach later. Each of those steps calls out its web GUI equivalent.
If you'd rather stick to plain YAML the whole way through, skip this
step entirely and go straight to step 3.

```bash
cd services/web-gui-frontend && npm install && npm run build && cd ../..
uv run web-gui-backend --config-root config
```

Visit `http://127.0.0.1:8420/` — one process serves both the API and
the built frontend (see `services/web-gui-backend/README.md`'s "Running
it as one process"). Leave it running in a terminal while you work
through the steps below; `Ctrl-C` to stop it, no state is lost (it only
ever reads/writes the same `config/`/`library/`/`state/` files the CLI
commands below do).

The Sync screen (step 7) additionally needs real access to the iPod's
block device, so it only works when this process runs on the same
machine the iPod is physically connected to — everything else (Sources,
Credentials, Podcasts, Profiles) works from anywhere. This is also
exactly what the [Docker path](#docker-recommended) above packages up
as one container, if you'd rather not run it bare metal at all.

### 3. Pick which sources you're using

Edit `config/global.yaml`'s `sources.*.enabled` flags — turn off
whichever of Apple Music / Spotify / YouTube Music you don't plan to
use. **Spotify is currently blocked** on a Premium API requirement
outside this project's control (see the Status table above); enabling
it won't actually download anything today. Podcasts aren't gated here —
each profile that wants them just sets its own Pocket Casts credentials
(step 4) and `podcasts:` block (step 5).

**Web GUI**: the "Sources & credentials" screen has the same enable
toggle for each source, right above that source's credential form
(step 4) — one screen for both.

### 4. Get credentials for each source you're using

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

*Web GUI*: paste the exported cookies straight into the "Sources &
credentials" screen's Apple Music card instead of placing the file
yourself — same destination path, same plaintext-on-disk caveat (shown
above the input).

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

*Web GUI*: the same screen's YouTube Music card takes pasted cookies,
and can drive the `ytmusicapi oauth` device-code flow for you (shows
the code + a link, then polls until you approve it) instead of running
that command yourself. The PO-token companion service (part 3 above)
still needs to be started separately either way — nothing in the web
GUI runs it for you.

**Podcasts (Pocket Casts)**: create
`config/secrets/pocketcasts/<you>.json`:
```json
{"email": "you@example.com", "password": "your-pocketcasts-password"}
```

*Web GUI*: enter the same email/password in the Podcasts screen once
you've created a profile (step 5) — it's validated against a real login
attempt before the file gets saved, so a typo is caught immediately
instead of surfacing later as a confusing fetch failure.

**Spotify**: shelved — see step 3, nothing to configure until the
upstream Premium API requirement is resolved.

### 5. Create your profile

```bash
cp config/profiles/alice.yaml config/profiles/<you>.yaml
```

Edit it — `alice.yaml`/`bob.yaml` document every field inline as
comments, so treat them as the reference. At minimum you'll set:
- `device.match_by`/`match_value` — see step 7 for how to find these
  once your iPod is connected.
- `playlists:` — one entry per playlist you want fetched, per source.
- `podcasts.pocketcasts.credentials_file` — pointing at the file from
  step 4.
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

**Web GUI**: the Profiles screen's "New profile" does the copy for you,
then the same screen edits `sync:` and `device.match_by`/`match_value`
— with the iPod already connected to the machine running the backend,
there's a "Detected device" shortcut that fills both device fields in
from a live scan instead of you running `lsblk` yourself (step 7's
manual version). The Sources and Podcasts screens are where
`playlists:`/`podcasts:` actually get ticked, once this profile exists
to tick them for.

### 6. First fetch

```bash
uv run music-stack fetch --profile config/profiles/<you>.yaml
```

Downloads every playlist across every enabled source, plus podcasts,
into `library/`. Safe to re-run any time — already-downloaded
tracks/episodes are skipped, not re-fetched.

*No web GUI equivalent yet* — the GUI configures schedules and
credentials, and shows fetch results after the fact (Overview/Activity
screens), but there's no "fetch now" button; run this by hand, or wait
for step 8's scheduled fetching once that's set up.

### 7. First device sync

Connect your iPod. If you don't already know its `device.match_value`
for step 5, the simplest option is `match_by: volume_label` — find it
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

**Web GUI**: the Sync screen does the same plan-review-execute flow
from the browser — "Compute plan" (read-only), review it (same
`to_remove` list), then "Execute sync" (asks to confirm before removals
are actually allowed). Only reachable if you started the GUI (step 2)
on this same machine, or via the [Docker path](#docker-recommended)'s
privileged container.

### 8. Automate it (optional)

Once the above works end to end by hand: scheduled, unattended fetching
is `uv run --project services/fetch-scheduler fetch-scheduler
--config-root config` (continuous, or `--once` under cron/a systemd
timer — full details in
[`services/fetch-scheduler/README.md`](services/fetch-scheduler/README.md)).
Fully-automatic device sync on connect needs a one-time manual install
(`sudo`, touches system udev/systemd config — deliberately not
automated):
[`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#automation-m9-auto-sync--udev).
Neither is required, both build on exactly the commands above.

**Web GUI**: the Sync screen's "Set up auto-sync" card generates the
exact, filled-in systemd unit + udev rule text and the `sudo` commands
for that same fully-automatic install — it never runs those commands
itself, so you still copy/paste and run them by hand once, same
one-time install either path.

### Reference: running individual pieces

Every step above composes smaller, independently-usable services — see
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
| [`music-stack-cli`](services/music-stack-cli/README.md) | The unified `music-stack fetch` command — also a Docker service (`music-stack`), see the Docker setup path above |
| [`fetch-scheduler`](services/fetch-scheduler/README.md) | Cron-scheduled fetching + automatic library/backup maintenance |
| [`sync-orchestrator`](services/sync-orchestrator/README.md) | Device sync engine (bare metal) + `auto-sync`/udev automation + `full-sync` (interactive fetch+device in one command) |
| [`audiobook-manager`](services/audiobook-manager/README.md) | Merges manually-acquired MP3 parts into a tagged, chaptered `.m4b` (ffmpeg + beets-audible) |
| [`web-gui-backend`](services/web-gui-backend/README.md) | FastAPI service for the web GUI (M11) — reads/writes `config/` through the same loader every CLI tool uses; also the one Docker service built with real (`--privileged`) device access, for its Sync screen |
| [`web-gui-frontend`](services/web-gui-frontend/README.md) | React/Vite SPA for the web GUI — Overview, Profiles, Music sources, Podcasts screens + credential capture forms so far |

Bare-metal device sync (this section's own step 7 above) needs the
iPod connected/mounted and can't run through Docker the way every
other CLI command in this section can — see "Why the sync step needs
privileged Docker access" up in the Docker section for the full
reasoning (the Docker path's own Sync screen works around this with a
`--privileged` container instead).

For fetch + device sync in one bare-metal command instead of the two
separate steps this section's step 6/7 use (same `cd
services/sync-orchestrator` as above, bare profile name, no other
paths needed) — see `full-sync` in
[`services/sync-orchestrator/README.md`](services/sync-orchestrator/README.md#one-command-fetch--device-full-sync):

```bash
uv run sync-orchestrator full-sync --profile <you> --config-root ../../config
```

**Docker, one source/task at a time** — beyond the `music-stack`/
`fetch-scheduler`/`web-gui-backend` services already covered in the
Docker section above, individual fetcher containers are also one-shot
and gated behind Compose profiles, one per music source (`apple`,
`spotify`, `ytmusic`), matching `global.yaml`'s `sources.*.enabled`
flags — useful to fetch just one source without touching `.env`'s
`COMPOSE_PROFILES`:

```bash
docker compose --profile apple up
docker compose --profile apple --profile spotify up   # multiple sources
```

`ytmusic` needs one more thing Compose doesn't manage: the
`bgutil-ytdlp-pot-provider` companion service must be running and
reachable before `fetcher-ytmusic` can actually download anything (not
just an optional nicety — every download fails without it). See
`services/fetcher-ytmusic/README.md` for setup.

`audiobook-manager` is a manual, one-book-at-a-time CLI tool (not tied
to any `global.yaml` flag, and pulling in beets' real dependency weight
— `numpy`/`scipy`/`numba`/`llvmlite`), so it's gated behind its own
profile and isn't built by default either way:

```bash
docker compose --profile audiobooks run --rm audiobook-manager \
    import-audiobook --parts-dir /data/library/... \
    --library-root /data/library/audiobooks --state-root /data/state
```

(bare metal: `uv run audiobook-manager import-audiobook --parts-dir
"path/to/Author - Title" --library-root library/audiobooks --state-root
state`.) `audiobook-manager discover --root <drop-zone> --state-root
state` (add the `--profile audiobooks run --rm audiobook-manager` shape
above for the Docker equivalent) scans a folder of raw, not-yet-
processed parts folders and flags which ones still need the
`import-audiobook` step above — the web GUI's Audiobooks screen wraps
the same thing (a "Discover new audiobooks" card that can kick off
processing without leaving the browser). Full details:
[`services/audiobook-manager/README.md`](services/audiobook-manager/README.md).

## Architecture

- **Docker vs. bare metal split**: acquisition/processing services
  (fetchers, library-manager, podcast-manager, fetch-scheduler) only read
  config and write to shared volumes, so they containerize cleanly. The
  *unattended*, udev-triggered auto-sync-on-connect stays bare metal
  (see `services/sync-orchestrator/README.md`) rather than trying to
  replicate udev hotplug handling inside a container — see `notes.md`'s
  "Distribution: why sync-orchestrator isn't containerized too" for why
  that specifically wasn't worth it for a background daemon. The web
  GUI's on-demand Sync screen is the one exception that does run
  containerized with real device access anyway (`--privileged`, see
  "Why the sync step needs privileged Docker access" above) — a good
  enough tradeoff for a button a
  human clicks, worse for what would otherwise be sync-orchestrator's own
  always-on hotplug listener.
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
