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

## Status

| Milestone | What | Status |
|---|---|---|
| M1 | Repo scaffold, config loader/validator | Done |
| M2 | Apple Music fetcher (`gamdl` wrapper) | Done |
| M3 | Spotify + YouTube Music fetchers | YouTube Music: done, downloads work end to end (needed a companion PO-token service for yt-dlp — see `services/fetcher-ytmusic/README.md`). Spotify: built and auth-working, but downloads are blocked on a Spotify Premium requirement for API access outside this project's control (see `notes.md`) |
| M4 | Library manager: cross-source dedup, playlist writer | Done |
| M5 | Podcast manager: Pocket Casts client, episode downloader | Done |
| M6 | iOpenPod headless spike: full real sync (music + playlists + podcasts) | Done — see [`docs/m6-ipod-headless-recommendation.md`](docs/m6-ipod-headless-recommendation.md) |
| M7 | Sync orchestrator core (`services/sync-orchestrator`) | Done — real device discovery + profile-driven sync plan, live-verified |
| M8 | Play-status round trip | Done for played/unplayed marking — live-verified with a real before/after state change. Resume-position sync doesn't work via the simple write endpoint used (see `notes.md`); Pocket Casts' real app likely needs its protobuf-based sync protocol for that, not yet built |
| M9+ | Automation, web GUI | Not started |

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) — all Python tooling runs
through it so nothing touches your system Python.

```bash
uv sync
uv run pytest   # runs the root workspace's tests, should all pass
```

`services/fetcher-spotify` and `services/sync-orchestrator` are separate,
standalone `uv` projects (see "Per-service usage" below) — their tests
run with their own `uv run pytest`, inside their own directories, not
picked up by the command above.

### Configuration

```
config/
├── global.yaml                    # shared source enable flags, credential paths
├── profiles/
│   ├── alice.yaml, bob.yaml        # example profiles — copy one to get started
│   └── <you>.yaml                  # your real profile — gitignored, never commit this
└── secrets/                        # real credentials — gitignored entirely
```

Copy an example profile (`config/profiles/alice.yaml` or `bob.yaml`) to
`config/profiles/<your-name>.yaml` and fill in your real device match info,
playlists, and Pocket Casts credentials path. Real per-user profiles and
everything under `config/secrets/` are gitignored — only the example
profiles are meant to be committed.

### Per-service usage

Each service under `services/` is an independent CLI — there's no single
"do everything" command yet (see `notes.md`'s CLI-ergonomics note), so a
full run means calling each of these in turn. All examples below assume
you're at the repo root and have a real profile at
`config/profiles/<you>.yaml` (see Configuration above); swap in your own
paths/playlist names.

**Music fetchers** — one `fetch` call per playlist, repeated for every
playlist in your profile:

```bash
# Apple Music (root workspace)
uv run fetcher-apple fetch \
    --profile config/profiles/<you>.yaml \
    --playlist "<playlist name from your profile>" \
    --cookies-path config/secrets/apple_music_cookies.txt \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-path state/<you>.sqlite

# YouTube Music (root workspace) — needs the PO-token companion service
# running first, see services/fetcher-ytmusic/README.md
uv run fetcher-ytmusic fetch \
    --profile config/profiles/<you>.yaml \
    --playlist "<playlist name from your profile>" \
    --cookies-path config/secrets/youtube_cookies.txt \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-path state/<you>.sqlite

# Spotify — standalone project, run from inside its own directory.
# Currently blocked on a Spotify Premium requirement (see Status above).
cd services/fetcher-spotify
uv run fetcher-spotify fetch \
    --profile ../../config/profiles/<you>.yaml \
    --playlist "<playlist name from your profile>" \
    --credentials-path ../../config/secrets/spotify_credentials.json \
    --library-root ../../library/music \
    --playlists-root ../../library/playlists \
    --state-path ../../state/<you>.sqlite
```

**Podcasts** — one call syncs every subscribed show at once (no
per-playlist repetition needed):

```bash
uv run podcast-manager sync \
    --profile config/profiles/<you>.yaml \
    --credentials-path config/secrets/pocketcasts/<you>.json \
    --library-root library/podcasts \
    --state-path state/<you>.sqlite
```

**Dedup** — run after fetching, before syncing to a device, to catch
the same song downloaded from more than one source:

```bash
uv run library-manager dedup \
    --library-root library/music \
    --playlists-root library/playlists \
    --state-dir state
```

**Device sync** — standalone project, needs the iPod connected and
mounted, must run on bare metal (see Architecture below):

```bash
cd services/sync-orchestrator
# Plan only first — prints what would change, writes nothing:
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state

# Once you've reviewed the plan (especially to_remove), write it for real:
uv run sync-orchestrator sync \
    --profile ../../config/profiles/<you>.yaml \
    --library-root ../../library \
    --state-root ../../state \
    --execute
```

See each service's own `--help` for the full flag list (lock timeouts,
`--skip-backup`, `--allow-removals`, etc.) — the commands above are the
common case, not the complete reference.

### Running with Docker

Fetcher containers are gated behind Compose profiles, one per music
source (`apple`, `spotify`, `ytmusic`), matching `global.yaml`'s
`sources.*.enabled` flags — a household that only uses Apple Music
doesn't need to build or run containers for the others. `library-manager`
and `podcast-manager` have no profile and always run.

```bash
docker compose --profile apple up
docker compose --profile apple --profile spotify up   # multiple sources
```

`ytmusic` needs one more thing Compose doesn't manage: the
`bgutil-ytdlp-pot-provider` companion service must be running and
reachable before `fetcher-ytmusic` can actually download anything (not
just an optional nicety — every download fails without it). See
`services/fetcher-ytmusic/README.md` for setup.

Or set `COMPOSE_PROFILES` in `.env` once instead of passing `--profile`
every time (see `.env.example`). Compose doesn't read `global.yaml`
itself, so keep the two in sync by hand — enabling a source there
without also enabling its profile here just means that fetcher's
container never runs.

## Architecture

- **Docker vs. bare metal split**: acquisition/processing services
  (fetchers, library-manager, podcast-manager) only read config and write
  to shared volumes, so they containerize cleanly. The iPod sync step needs
  real USB device access and runs on bare metal.
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
