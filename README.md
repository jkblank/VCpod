# VCpod (Vibe-Coded pod)

A personal "*arr-stack for music" — acquires music and podcasts from
streaming sources you already subscribe to, organizes and tags it, and
syncs it onto a real click-wheel iPod. No streaming/serving component: this
is a pipeline that ends at a physical device, not a Navidrome/Jellyfin
alternative.

Built almost entirely through AI-assisted ("vibe-coded") pair programming
with Claude Code — hence the name.

## What it does

1. **Acquires** music from streaming sources (Apple Music, Spotify) based on
   config-defined playlists.
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
| M3 | Spotify + YouTube Music fetchers | In progress — both built, with metadata/auth working, but downloads are currently blocked on platform-side requirements outside this project's control (see `notes.md`): Spotify needs Premium for API access, YouTube Music needs a PO-token provider service for yt-dlp to get past YouTube's bot-check |
| M4 | Library manager: cross-source dedup, playlist writer | Done |
| M5 | Podcast manager: Pocket Casts client, episode downloader | Done |
| M6 | iOpenPod headless spike: full real sync (music + playlists + podcasts) | Done — see [`docs/m6-ipod-headless-recommendation.md`](docs/m6-ipod-headless-recommendation.md) |
| M7 | Sync orchestrator core (`services/sync-orchestrator`) | Done — real device discovery + profile-driven sync plan, live-verified |
| M8+ | Play-status round trip, automation, web GUI | Not started |

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) — all Python tooling runs
through it so nothing touches your system Python.

```bash
uv sync
uv run pytest   # 81 tests, should all pass
```

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

Each service under `services/` is an independent CLI:

```bash
uv run fetcher-apple fetch --profile config/profiles/<you>.yaml ...
uv run podcast-manager sync --profile config/profiles/<you>.yaml ...
uv run library-manager dedup ...
```

`services/fetcher-spotify` and `services/sync-orchestrator` are
standalone `uv` projects (heavy/conflicting dependencies kept out of the
shared root workspace) — run their commands from inside those
directories.

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

`gamdl` (Apple Music) and the Spotify fetcher operate in a legal/ToS gray
area — they're personal-use tools for downloading music you already have
access to via your own paid subscription, not intended for redistribution
or exposure as a public service. Use accordingly.

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
