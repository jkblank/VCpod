# Music Management Stack — Planning Doc

## 1. Goal

An "*arr-stack for music" that has no streaming/serving component. It acquires
music from multiple streaming sources based on config-file-defined playlists,
organizes/tags it, and syncs it (plus podcast episodes) onto one or more
click-wheel iPods. Pocket Casts is the source of truth for podcast
subscriptions and played/unplayed state. Everything is driven by text
configs, supports multiple user/iPod profiles, and runs across a mix of
Docker (for acquisition/processing) and bare metal (for USB/device access).

**Build order (this doc's structure follows it):**
1. Acquisition + playlist file generation → write into a library directory.
2. Podcast acquisition + Pocket Casts state sync.
3. Automated iPod sync orchestration (built on iOpenPod), including
   play-status round trip.

---

## 2. Repo / container layout

```
music-stack/
├── docker-compose.yml
├── .env.example
├── config/
│   ├── global.yaml
│   ├── profiles/
│   │   ├── alice.yaml
│   │   └── bob.yaml
│   └── secrets/                  # gitignored
│       ├── apple_music_cookies.txt      # shared source login (see note below)
│       ├── spotify_credentials.json
│       ├── ytmusic_oauth.json
│       └── pocketcasts/
│           ├── alice.json        # per-profile Pocket Casts account
│           └── bob.json
├── services/
│   ├── common/                   # shared lib: config models, state db, logging
│   │   ├── config.py
│   │   ├── state.py
│   │   └── models.py
│   ├── fetcher-apple/            # gamdl wrapper
│   │   ├── Dockerfile
│   │   └── fetch.py
│   ├── fetcher-spotify/          # zotify wrapper
│   │   ├── Dockerfile
│   │   └── fetch.py
│   ├── fetcher-ytmusic/          # ytmusicapi + yt-dlp
│   │   ├── Dockerfile
│   │   └── fetch.py
│   ├── library-manager/          # beets-based tagging/dedup + playlist writer
│   │   ├── Dockerfile
│   │   └── organize.py
│   ├── podcast-manager/          # Pocket Casts client + episode downloader
│   │   ├── Dockerfile
│   │   └── podcasts.py
│   └── sync-orchestrator/        # BARE METAL — needs USB access to the iPod
│       ├── sync.py
│       └── ipod_backend/         # vendored/wrapped iOpenPod modules
├── library/                      # shared volume — acquisition output
│   ├── music/{Artist}/{Album}/{Track}.m4a
│   ├── playlists/{profile}/{playlist_name}.m3u8
│   └── podcasts/{show}/{episode}.mp3
├── state/
│   └── {profile}.sqlite          # source-id → local file map, sync history
└── docs/
    └── this file
```

**Why this split:** fetchers, library-manager, and podcast-manager are
stateless-ish, containerizable, and don't need hardware access — they just
read config and write to the `library/` and `state/` volumes. The
sync-orchestrator is the one piece that must run bare metal (or a privileged
container with `--device` passthrough) because it needs to see the iPod as a
mounted USB block device.

---

## 3. Config schema

### `config/global.yaml`
```yaml
paths:
  library_root: /data/library
  state_root: /data/state

sources:
  apple_music:
    enabled: true
    cookies_file: /config/secrets/apple_music_cookies.txt
  spotify:
    enabled: true
    credentials_file: /config/secrets/spotify_credentials.json
  ytmusic:
    enabled: false
    oauth_file: /config/secrets/ytmusic_oauth.json

podcasts:
  pocketcasts:
    poll_interval_minutes: 60   # default; Pocket Casts credentials are per-profile, not global — see profile schema below
```

**Note on the asymmetry here:** `apple_music`/`spotify`/`ytmusic` under
`sources:` are modeled as shared logins (one household subscription used
across profiles), which matches how those services are typically set up
for this kind of stack. Pocket Casts is different — each user has their
own account and their own subscriptions/listen state — so its credentials
live under each profile instead of globally. If any of the music sources
also need to be per-user for you, that's a small schema change (move that
block under `profiles/*.yaml` the same way), just flag it.

### `config/profiles/alice.yaml`
```yaml
profile: alice
device:
  match_by: serial          # serial | volume_label
  match_value: "AA11BB22"

fetch:
  schedule: "0 3 * * *"     # profile-default fetch schedule (cron expression) —
                             # applies to every playlist/show below unless overridden

playlists:
  - name: "Workout Mix"
    source: apple_music
    source_id: "pl.u-abc123"
  - name: "Chill"
    source: spotify
    source_id: "37i9dQZF1DX..."
    fetch_schedule: "0 */6 * * *"   # overrides fetch.schedule for this playlist only

podcasts:
  pocketcasts:
    credentials_file: /config/secrets/pocketcasts/alice.json   # this user's own account
  sync_unplayed_only: true
  max_episodes_per_show: 5
  fetch_schedule: "0 * * * *"       # overrides fetch.schedule for all shows below
  shows:
    - "Daily News"                  # uses podcasts.fetch_schedule (or profile default)
    - "Weekly Deep Dive":
        fetch_schedule: "0 6 * * 1" # per-show override, same string/mapping mix
                                     # already used by external_library.selections
    # shows: all                    # (still supported as a shorthand for "every subscription")

sync:
  trigger: on_connect       # on_connect | manual | cron
  transcode_format: alac
  push_play_status_back: true
```

Each profile is self-contained; adding a user/iPod is "drop in a new YAML
file," no code changes.

**Two independent schedules, not one:** `fetch.schedule` (and its
per-playlist/`podcasts.fetch_schedule`/per-show overrides) controls when
data is *acquired* into `library/` — this runs regardless of whether any
iPod is connected, so the moment a device shows up, the freshest data is
already sitting there ready to write. `sync.trigger` controls when that
already-acquired data gets *written to the device* — still fired by
`on_connect` (or `manual`/`cron`) as before. Resolution precedence for the
fetch schedule is most-specific-wins: a playlist's own `fetch_schedule` >
`podcasts.fetch_schedule` for that show > the profile-level `fetch.schedule`
default.

---

## 4. Phase 1 — Acquisition & playlist creation

**Playlist discovery — how playlists get into a profile's config.**
Every source we're using requires the profile's own account credentials
for downloading anyway (Apple Music cookies, Spotify librespot session,
YouTube Music OAuth), and that same session is enough to enumerate the
account's full playlist library — no separate/extra API access needed:
- Apple Music: the Media User Token pulled from cookies can call
  `GET /v1/me/library/playlists` → list of `{id, name, canEdit, ...}`.
- Spotify: zotify's librespot session already supports listing the
  account's saved playlists directly (same mechanism its `-p` flag uses).
- YouTube Music: `ytmusicapi.get_library_playlists()` on the authenticated
  session.

Each fetcher therefore exposes **two** operations, not just "download":
- `list_playlists(profile_credentials) -> [{source_id, name, track_count, owner}]`
- `fetch_playlist(profile_credentials, source_id) -> tracks written to library/`

This is what lets the GUI (Phase 4) present a **picker** — "here are your
Apple Music/Spotify/YouTube Music playlists, tick the ones to sync" —
rather than requiring the user to hunt down and paste share links. The
config still just stores the resolved `source` + `source_id` (+ a display
name) once a playlist is chosen, so the schema in §3 doesn't change.

**Manual entry stays as a fallback**, for cases discovery can't reach —
e.g. a playlist someone else shared with you that isn't saved to your own
library. In that case the GUI (or a config edit) accepts a pasted URL,
and each fetcher's URL parser resolves it to a `source_id` the same way
`fetch_playlist` expects.

**Fetcher service contract (download side):**
- Input: a playlist entry from a profile config (`source`, `source_id`, `name`).
- Output:
  - Audio files written to `library/music/{Artist}/{Album}/{Track}.ext`,
    tagged with at least: title, artist, album, track/disc number, ISRC
    (when available), source + source-track-id (custom tag, for dedup).
  - An `.m3u8` playlist file written/updated at
    `library/playlists/{profile}/{playlist_name}.m3u8`, referencing the
    absolute or relative paths of the tracks in order.
  - A row per track in `state/{profile}.sqlite` mapping `source_id` →
    local file path, so re-runs skip already-downloaded tracks.
- Tools: `gamdl` (Apple Music), `zotify` (Spotify), `ytmusicapi` + `yt-dlp`
  (YouTube Music).

**library-manager** runs after fetchers, per sync cycle:
- Runs `beets` (or equivalent) over new files for tag cleanup/artwork.
- Dedups across sources: match on ISRC first, fall back to fuzzy
  artist+title match, prefer the highest-fidelity source when the same
  song came from two places.
- Rewrites `.m3u8` playlists to point at the deduped canonical file.

**Open item to validate first:** confirm whether iOpenPod can bulk-import a
directory of `.m3u8` files as playlists automatically, or whether the
sync-orchestrator needs to parse them itself and call iOpenPod's playlist
APIs directly. Spike this before building out all three fetchers.

---

## 5. Phase 2 — Podcasts / Pocket Casts

**podcast-manager:**
- Runs once per profile, authenticating with that profile's own Pocket
  Casts credentials (`profiles/{name}.yaml` →
  `podcasts.pocketcasts.credentials_file`) against the unofficial API
  (`api.pocketcasts.com`, bearer token). No shared/global Pocket Casts
  login — every request is scoped to the individual user's account, since
  subscriptions and listen state are personal per profile.
- Per profile, pulls: subscribed podcasts, per-episode played/unplayed
  status (`get_subscribed_podcasts`, `get_in_progress`-equivalent calls).
- For each profile's `podcasts.shows` filter, downloads audio for
  unplayed episodes directly from the podcast's own RSS enclosure URL
  (not through Pocket Casts) into `library/podcasts/{show}/`. Note: if two
  profiles subscribe to the same show, the audio file itself can still be
  shared/deduped in `library/podcasts/`, even though each profile's
  played/unplayed state and account are tracked separately.
- Writes an episode manifest to `state/{profile}.sqlite`: episode UUID,
  local file path, played/unplayed, position.
- Respects `max_episodes_per_show` and `sync_unplayed_only`.

This phase is independent of the iPod — it just needs to produce a correct,
current "what should be on the device" list plus the files themselves.

---

## 6. Phase 3 — Sync orchestration (iOpenPod-based)

- Runs on the bare-metal host (has USB access).
- A separate, always-running scheduler (not gated on device presence) fires
  Phase 1/2 fetches per each playlist's/show's/profile's `fetch_schedule` —
  see §3's "two independent schedules" note. This is what makes
  `on_connect` sync fast/correct: the device-write step never blocks on a
  fetch, it just diffs against whatever's already in `library/`.
- Detects iPod on connect (via udev rule → triggers script, or manual run).
- Matches the connected device to a profile by serial/volume label.
- Builds a sync plan: profile's playlists (from `library/playlists/`) +
  profile's podcast manifest (from Phase 2) → diff against what's currently
  on the device (iOpenPod's fingerprint-based diffing).
- Executes the sync via iOpenPod's `SyncEngine` / `iTunesDB_Writer` /
  `PodcastManager` modules, invoked headlessly (no GUI).
- After sync, reads back play counts / bookmark positions for podcast
  episodes that were previously on the device, and pushes "played" /
  position updates back to Pocket Casts (`update_playing_status`,
  `update_played_position`).

**Key open risk:** iOpenPod is currently packaged and documented as a
PyQt6 desktop app, not a library. First task in this phase is a spike:
can `SyncEngine`/`iTunesDB_Writer` be imported and driven headlessly from
a script against a real device, or does the GUI layer need to be
decoupled/forked first? This determines whether Phase 3 is "wrap an
existing library" or "fork and refactor iOpenPod."

---

## 7. Phase 4 — Web GUI (built last, after Phases 1–3 are working)

**Principle: the GUI is a thin layer over the config files, not a new
source of truth.** Every YAML file under `config/` remains the real state;
the GUI reads it to render, and writes back to it (through the same schema
loader/validator used by the CLI/services) when the user makes a change.
No parallel database of settings — this keeps the "configurable through
textfile configs as far as possible" property intact, and means someone
can still hand-edit YAML and have the GUI pick it up.

**Architecture:**
- Backend: a small FastAPI service (`services/web-gui/backend/`) that
  imports `services/common/config.py` directly — same load/validate/save
  logic as everything else, no reimplementation.
- Frontend: a simple SPA (htmx or a lightweight React app is enough; no
  need for anything heavier for a single/few-user local tool).
- Talks to the rest of the stack only through: (a) reading/writing files
  under `config/` and `state/`, and (b) triggering the sync-orchestrator
  and fetcher services as subprocesses/API calls, then tailing their logs.

**Scope of what the GUI manages:**
- Profiles: create/edit/delete `config/profiles/*.yaml` (device match,
  sync trigger, transcode format, etc.).
- Playlists per profile: primary flow is a **picker** — call each source's
  `list_playlists` (via that profile's own credentials) and let the user
  tick which ones to sync, rather than hand-typing source IDs. A manual
  "paste a URL" fallback covers playlists not owned by the account (e.g.
  ones shared with the user but not saved to their library).
- Podcast settings per profile: toggle shows, `max_episodes_per_show`,
  `sync_unplayed_only`.
- Global source config: enable/disable sources, point at credential files
  (the GUI should never display raw cookie/token contents — file paths
  and a "valid / expired, re-auth needed" status only).
- Per-profile Pocket Casts account: since this is scoped per user, show
  its connection/auth status inside that profile's editor (not the global
  source config), with the same "valid / needs re-auth" treatment.
- Sync visibility: trigger a manual sync, see current/last sync plan and
  result per profile, surface the health checks from Phase 3 (cookie
  expiry, Pocket Casts API failures) as visible alerts rather than buried
  log lines.

**Explicit non-goals for the GUI:** no in-browser audio playback, no
account management/auth system beyond basic local access control, no
reimplementation of fetcher/sync logic — it only orchestrates and edits
config.

---

## 7a. Phase 5 — Audiobooks via Libby/OverDrive (spike first)

A new content type alongside music/podcasts: audiobooks borrowed through
Libby (the consumer app for OverDrive, used by public libraries). Unlike
Phases 1–4, **the risky unknown here is acquisition, not the device
side** — a 2026-07-21 spike (see `notes.md`) found this inverted from
the initial assumption:

- **iOpenPod already has real audiobook support, simpler than the
  original spike described** (corrected 2026-07-27 against the actual
  source, not a paraphrase): a plain file already gets classified as an
  audiobook during iOpenPod's normal headless library scan — the exact
  rule (`iopenpod/sync/pc_library.py`) is `.m4b`/`.aa`/`.aax` extension
  **or** an MP4 `stik` atom of `2` — and `remember_position` (resume
  playback) is then automatically derived from that classification
  (`_track_conversion.py`), no extra wiring needed. **`album_chapters.py`
  is not part of this pipeline** — it's a GUI-only "convert an existing
  *music* album into one chaptered track" utility, invoked interactively
  from iOpenPod's own desktop app, and it explicitly sets
  `is_audiobook = False` on its output. It's irrelevant to acquiring
  audiobooks from an external source; the merge-into-one-chaptered-file
  step we need is a plain `ffmpeg` concat + `FFMETADATA` chapters job we
  build ourselves (well-documented, standard technique), not something
  to reuse from iOpenPod.
- **The acquisition tooling this would wrap is not viable today —
  confirmed via live testing against a real account, round 2
  (2026-07-27), not just inferred from old issues.** `odmpy` (the
  natural equivalent to `gamdl`/`zotify`/`yt-dlp` for this source) has
  been unmaintained since September 2023. OverDrive killed the
  `.odm`/legacy API entirely on January 31, 2025. Its separate `libby`
  setup-code auth flow (which looked like it might sidestep the dead
  `.odm` path) was tested directly: the current Libby app no longer
  exposes a generic "new device" code-generation option at all — only
  named partner integrations (Sonos) — and that code is rejected by
  `odmpy`'s generic auth request (confirmed twice, including a
  purpose-built script to rule out the code's ~60s TTL as the cause).
  `bookbonobo/libby-download-extension` (Firefox extension) is also
  stale — last commit July 2024, dependency bump only. See `notes.md`
  for the full round-2 writeup.
- **A real manual acquisition method exists, found in round 2**:
  Libby's *web* player (libbyapp.com, desktop Chromium browser only)
  streams each audiobook as plain per-chapter MP3 segments over HTTP,
  visible and downloadable via browser DevTools' Network tab — no root,
  no DRM defeat, no paid tool. More manual repetition for longer books,
  but genuinely current and working. Because each segment is a plain
  captured HTTP URL (not a broken encryption scheme), this also means a
  *scripted-browser* (Playwright) automation path looks more tractable
  than the original spike assumed, if full automation is wanted later.
- **Metadata tagging has a real answer**: `beets-audible` (the real,
  actively-maintained `Neurrone/beets-audible` on PyPI — author→artist,
  narrator, series, cover art via Audible/Audnex) — implemented as its
  own service, **not** folded into `library-manager` (see below).

**Decision after round 2, now shipped (2026-07-27)**: skip the
`fetcher-audiobooks`-as-automated-service scope — built the **manual
drop-in workflow** instead, as `services/audiobook-manager/`. Acquisition
stays manual (DevTools method above); this new service handles
everything after that: merging the raw MP3 parts into one chaptered
`.m4b` via `ffmpeg` (concat demuxer + FFMETADATA chapters, AAC-encoded),
then `beets-audible` tagging via `beet import -q` against a project-
managed beets config/db (isolated from any real user beets install).
It's its own standalone workspace member rather than a `library-manager`
subcommand — `beets` 2.12 pulls in a real, unconditional dependency
tree (`numpy`/`scipy`/`numba`/`llvmlite`) plus live Audnex/Audible
network calls, a `podcast-manager`-shaped operational profile that
would have made `library-manager` (deliberately kept minimal/offline)
much heavier for every user, not just audiobook users. `beet import -q`
skips (doesn't force-tag) any book it can't confidently match — the CLI
surfaces this loudly (exit 1, merged file's path, exact retry command),
never silently swallowed. See `services/audiobook-manager/README.md`.

**Shape, as actually implemented**: `library/audiobooks/{Author}/{Album}/
{Title}.m4b` plus `cover.png`/`desc.txt`/`reader.txt` sidecars — one
merged, chaptered file per book, but nested one level deeper than the
original flat `{Author}/{Title}.m4b` assumption (beets-audible's own
default `paths:` layout, accepted as-is rather than overridden — its
docs warn against changing path format after import, and it costs
nothing here since iOpenPod's media-folder scanner recurses by default
and filters strictly by audio extension, so the sidecar files and extra
folder depth are both transparently ignored/handled). No `.m3u8` needed.
`library/audiobooks/` syncs today via `sync-orchestrator`'s existing
`--pc-folder` flag — confirmed live, **zero new sync-orchestrator code**
was needed. It's a manual per-sync flag, not a persistent default yet;
making it permanent (editing `sync.py`'s own `pc_folders` tuple) is a
natural small follow-up once proven on a real device, deliberately left
out of this pass.

---

## 8. Cross-cutting risks to flag early

1. **iOpenPod headless usability** — see above, validate before Phase 3 work.
2. **gamdl cookie expiry** — Apple Music cookies need manual re-export every
   few weeks; add a health check that fails loudly (not silently) when auth
   expires.
3. **Pocket Casts API stability** — unofficial, undocumented, can break
   without notice. Wrap all calls with error handling and a read-only
   degraded mode.
4. **Podcast play-state fidelity** — verify how much of "listened" state the
   iTunesDB / iPod bookmark file actually exposes back to the host; this
   determines how accurate the round-trip to Pocket Casts can be.
5. **Cross-source dedup accuracy** — ISRC isn't always present (especially
   from YouTube Music); fuzzy matching will have false positives/negatives,
   worth a manual-review escape hatch.
6. **Legal/ToS posture** — gamdl and similar tools operate in a gray area;
   this is a personal-use tool, not something to expose or distribute.
7. **Libby/OverDrive acquisition tooling viability** — resolved (see 7a):
   the natural tool to wrap (`odmpy`) is confirmed dead against Libby's
   current app, and no automated alternative exists. Acquisition stays
   manual; `services/audiobook-manager/` handles everything downstream
   of that (merge, tag, sync).

---

## 9. Milestones

| # | Milestone | Acceptance criteria |
|---|-----------|---------------------|
| M1 | Repo scaffold, config loader, profile schema validation | `global.yaml` + profile YAMLs load and validate; invalid config fails with a clear error |
| M2 | Apple Music fetcher end-to-end | Given a playlist config entry, downloads tracks via gamdl, tags them, writes `.m3u8`, records state db rows; `list_playlists` correctly returns the account's library playlists |
| M3 | Spotify + YouTube Music fetchers | Same contract as M2 (including `list_playlists`), both sources working |
| M4 | Library manager (tagging/dedup) | Cross-source duplicate correctly collapsed to one file; playlists point at canonical file |
| M5 | Pocket Casts client | Subscriptions + unplayed episodes correctly listed; episodes downloaded from RSS |
| M6 | iOpenPod headless spike | Written recommendation: use-as-library vs fork, with a working proof-of-concept script that writes at least one track to a real device without the GUI |
| M7 | Sync orchestrator core | Given a profile + connected device, produces and executes a correct sync plan (music + podcasts) |
| M8 | Play-status round trip | Episodes played on-device are correctly marked played in Pocket Casts after next sync |
| M9 | Automation | udev-triggered sync on device connect, multi-profile device matching working, **and** profile data (playlists + podcasts) fetched/updated on each's configured schedule independently of device presence, so the latest data is already in `library/` by the time the device is plugged in |
| M10 | Hardening | Secrets handling reviewed, health checks/alerts for auth expiry and API failures, basic docs |
| M11 | Web GUI backend | FastAPI service reads/writes profiles and global config through the shared `common/config.py` loader; validation errors surface to the caller |
| M12 | Web GUI frontend — profiles & playlists | Create/edit/delete profiles; add playlists via the source picker (list_playlists) or manual URL paste fallback; changes are reflected correctly in the underlying YAML files |
| M13 | Web GUI — podcasts & sources | Manage per-profile podcast settings and global source enable/disable + credential status through the UI |
| M14 | Web GUI — sync visibility | Trigger a manual sync from the UI, view live/last sync plan and result, see health-check alerts (cookie expiry, API failures) |
| M15 | Audiobooks via Libby/OverDrive | Spike: headless-viable acquisition path confirmed (automated) or a documented manual-drop-in workflow (fallback); at least one real audiobook acquired, tagged via `beets-audible`, and synced to a real device with correct chapter/resume behavior on-device |

---

## 10. Explicit non-goals

- No streaming/serving of music (no Navidrome/Jellyfin-for-music equivalent).
- No iPod touch / iOS device support (clickwheel only, per current profiles).
- No in-browser audio playback or user-account system in the web GUI —
  it's a local config/orchestration front-end, not a media app.
- Web GUI is deliberately last (Phase 4) — it's a layer on top of a
  working, config-file-driven pipeline, not a dependency of it.
