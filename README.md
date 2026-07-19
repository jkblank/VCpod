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
| M3 | Spotify fetcher | Shelved — upstream Spotify/librespot auth instability |
| M4 | Library manager: cross-source dedup, playlist writer | Done |
| M5 | Podcast manager: Pocket Casts client, episode downloader | Done |
| M6 | iOpenPod headless spike: full real sync (music + playlists + podcasts) | Done — see [`docs/m6-ipod-headless-recommendation.md`](docs/m6-ipod-headless-recommendation.md) |
| M7+ | Sync orchestrator, play-status round trip, automation, web GUI | Not started |

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

`services/fetcher-spotify` and `services/ipod-sync` are standalone `uv`
projects (heavy/conflicting dependencies kept out of the shared root
workspace) — run their commands from inside those directories.

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

## License

MIT — see [`LICENSE`](LICENSE).
