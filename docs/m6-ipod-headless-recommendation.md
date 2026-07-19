# M6: iOpenPod headless spike — recommendation

**Recommendation: use iOpenPod as a library, not a fork.**

## Acceptance criteria (from `music-stack-planning.md`)

> Written recommendation: use-as-library vs fork, with a working
> proof-of-concept script that writes at least one track to a real device
> without the GUI.

Both parts are done, and the PoC went well beyond the minimum bar. The
script (`services/ipod-sync/spike/headless_write_poc.py`) ran a real full
sync against a real, connected iPod (5.5-gen "iPod Video", 160GB, serial
`000A270015AE6188`) without ever importing or launching iOpenPod's PyQt6
GUI:

- **Music**: 400 new tracks added, 4,370 tracks got metadata updates
  (release dates, lyrics, sound-check, minor tag corrections), 216 play
  counts and 11 ratings synced back from the device, 0 removals. Verified
  by re-parsing the on-device iTunesDB afterward: track count went from
  4,559 → 4,959, exactly matching the plan.
- **Playlists**: all 7 real playlists (ALT CTRL, Chill, Elevate, Every1,
  New Music, Rise Up, Rise And Grind) correctly discovered and synced —
  see the playlist-path bug below for what it took to get there.
- **Podcasts**: integrated via iOpenPod's dedicated podcast API
  (`build_podcast_sync_plan`), not file-tag scanning — see the podcast
  section below. Merged into the same `SyncEngine` execute as music and
  playlists, in one combined run.

## Why "use as library"

Investigated the real GitHub source (`https://github.com/TheRealSavi/iOpenPod`,
MIT, tag `v1.66.2` — the AUR/PyPI install is a compiled PyInstaller bundle
with no readable source, so the source had to be cloned separately).

- `docs/adr/0001-sync-session-orchestration.md` states the design intent
  directly: the Sync Session module owns sync orchestration "without
  making app-core depend on the GUI widget tree."
- Grepping for `PyQt6`/`iopenpod.gui` imports across `src/iopenpod/`
  outside of `gui/` found **zero hits** in `sync/`, `device/`,
  `itunesdb_writer/`, `itunesdb_parser/`, `infrastructure/`, `podcasts/`.
  PyQt6 is confined to `gui/` and to a handful of `application/` files
  that are QThread-based worker wrappers bridging progress signals to the
  desktop UI (`jobs.py`, `runtime.py`, `sync_session.py`,
  `controllers.py`, `bootstrap.py`).
- `sync/core/engine.py`'s `SyncEngine` is a plain class with a typed,
  dataclass-based request/outcome facade (`sync/core/models.py`) — the
  same facade the GUI itself calls through, directly callable with no Qt
  involved.
- Real, already-tested building blocks exist and were used directly:
  `device.info.DeviceInfo`/`enrich()` (device identification — "the ONE
  place in the entire codebase that touches hardware"),
  `itunesdb_parser.ipod_library.load_ipod_library()` (read the on-device
  DB, explicitly documented as "standalone parser service, no GUI
  dependency"), `sync.backup_manager.BackupManager` (a real,
  content-addressable full-device backup/restore tool, used as the safety
  net before every write attempt in this spike), and
  `podcasts.podcast_sync.build_podcast_sync_plan` (see below).
- Published on PyPI as `iopenpod==1.66.2` (verified live), installable as
  a normal pinned dependency in a standalone uv project
  (`services/ipod-sync/`), same pattern as `services/fetcher-spotify/`
  for a dependency-heavy package kept out of the shared root workspace.

## Podcasts: no file-tag dependency needed

`podcasts/models.py`'s `PodcastEpisode`/`PodcastFeed` are plain
dataclasses (`guid`, `title`, `audio_url`, `downloaded_path`,
`duration_seconds`) — directly constructible from `podcast-manager`'s own
episode state DB, with **no dependency on embedded audio file tags** (no
`stik=21`/`pcst` MP4 atoms, no ID3 `WFED`/`PCST` frames). Checked a real
downloaded episode's tags directly — podcast-manager's raw RSS-enclosure
downloads have none of these, so the generic PC-folder media scan
(`pc_library.py`) would have misclassified them as plain music.

Instead: `podcasts.podcast_sync.build_podcast_sync_plan(episodes,
ipod_tracks)` takes `(PodcastEpisode, PodcastFeed)` pairs directly and
returns a real `SyncPlan` — the same type the music/playlist PLAN
produces — with correct podcast media-type flags, matching existing
device tracks by enclosure URL or title+album to avoid duplicates. The
two plans are merged the same way the real app does it
(`application/sync_session.py`): extend `to_add`, sum `storage`.

The **read-back side exists too**, which matters for the planning doc's
M8 (play-status round trip to Pocket Casts): `match_ipod_tracks()` +
`_update_episode_playback_from_track()` read `play_count_1`/
`recent_playcount`/`last_played` straight off the same parsed track dicts
`load_ipod_library()` already returns, and write them onto the
`PodcastEpisode` object (`play_count`, `last_played`,
`listened_override`). Not yet wired into `podcast-manager`'s Pocket Casts
API client — that's the actual M8 work — but the iOpenPod side of the
round trip is confirmed to exist and require no extra code from us.

This did require one real schema change: `common.state.EpisodeRecord`
didn't persist episode `title`/`audio_url`/`duration_seconds` (needed for
`build_podcast_sync_plan`'s dedup matching). Added with an in-place
migration for the existing state DB — see `notes.md`.

## One real nuance: `application`'s `__init__.py` is not itself Qt-free

Individual files under `application/` — `context.py`, `services.py`,
`sync_plan_builder.py`, `sync_options.py`, `device_access.py` — have no
direct PyQt6 imports. But `application/__init__.py` eagerly imports
`.jobs` and `.sync_session` (both genuinely PyQt6-dependent) as part of
the package's own module init, so importing *any* name under
`iopenpod.application.*` transitively loads PyQt6 into `sys.modules`
regardless of which specific file it lives in. The PoC avoids this
entirely by never importing anything from `iopenpod.application` — it
builds the small amount of device-capability/storage snapshot logic it
needs directly from `device.info.DeviceInfo`, rather than reusing
`application.services.DeviceCapabilitySnapshot`/`DeviceStorageSnapshot`.

This isn't disqualifying — PyQt6 doesn't require a display or a running
`QApplication` merely to be imported, so even if a future integration does
pull in `application.*`, "headless" (no GUI window shown) still holds. But
it does mean the real library boundary for a clean implementation is
`device`/`itunesdb_parser`/`itunesdb_writer`/`sync.core`/
`sync.backup_manager`/`podcasts` — not `application`, despite
`application` being architecturally closer to "the code the GUI calls."

## Real problems hit and resolved during the PoC

Full detail in `notes.md`. Summary:

1. **`fpcalc` (chromaprint) is a hard dependency** of `FingerprintDiffEngine.compute_diff`,
   not optional — installed via `pacman -S chromaprint`.
2. **`SyncEngine`'s PLAN treats `pc_folders` as the complete authoritative
   source**, not "add these on top of what's there." Pointing it at a
   narrow scratch folder with one track produced a plan proposing to
   remove all 4,559 existing tracks. The PoC's built-in safety check
   (refuse to execute anything but a clean plan) caught this before
   anything was written — this worked exactly as designed. Fixed by
   pointing `pc_folders` at the real, complete PC libraries and the real
   playlists folder instead of a scratch directory.
3. **iopenpod's device-side fingerprint cache is never persisted to
   disk** — root-caused, not just observed: `FingerprintCache.save()` is
   only ever called after the PC-side library scan, never after
   device-side track fingerprinting, even though `cache.store()` runs
   correctly for device tracks too. Confirmed by inspecting the real
   cache file directly: 4,960 entries, all PC-side, zero for the iPod,
   despite multiple runs against the same device. Worked around by
   forcing our own `FingerprintCache.get_instance().save()` call right
   after PLAN completes, in our own script.
4. **No cover-art format data for this device generation** — this
   iopenpod version's model tables only cover 6th/6.5th/7th-gen "iPod
   Classic," not this 5th/5.5th-gen "iPod Video." Worked around by
   monkeypatching iopenpod's internal device-capability resolution
   (`iopenpod.device.get_current_device_for_path`/
   `capabilities_for_family_gen`) to force `supports_artwork=False`,
   which iopenpod itself already has a graceful fallback path for once
   that value actually reaches the write path.
5. Two backup-and-retry cycles left orphaned audio files on the device
   (files copied before a later DB-write step failed and correctly rolled
   back the database). Cleaned up precisely both times by diffing the
   `BackupManager` snapshot manifest against the live device filesystem.
6. **Playlist files written via our own `fetcher-apple`'s per-track
   fallback path had relative paths**, not absolute — a bug in our code,
   not iopenpod's. iopenpod's playlist-file sync skipped nearly every
   entry in two playlists (25/25 and 20/21) as a result. Fixed in
   `fetcher_apple.download.fetch_playlist` (resolve `library_root` to
   absolute at the top) plus a regression test; the two already-broken
   `.m3u8` files were repaired in place.
7. **`podcast-manager`'s episode downloads had no per-episode or
   per-show resilience** — one bad download (a large, ~127MB episode
   that kept dropping mid-transfer) crashed the entire multi-show sync,
   never even attempting the remaining shows. Fixed with per-episode
   retry-with-backoff plus per-show exception isolation; investigating
   the actual failures showed a real pattern (every failure was a
   30-90 minute episode, across 3 unrelated CDN hosts) rather than one
   bad podcast or host.

None of these blocked the "use as library" conclusion — they're real
integration details of a large, actively-developed third-party tool (plus
a couple of real bugs in our own code, found only because we exercised
the full real path), not signs that headless use is unworkable. The final
combined run (music + playlists + podcasts, one `SyncEngine` execute)
completed a real, verified, correct full sync.

## What M7 should reuse from this spike

- The core PLAN → inspect → EXECUTE → verify pattern, with the safety
  check (refuse to execute a plan containing unexpected removals) kept as
  a hard gate, not just spike-script caution.
- `BackupManager.create_backup()` as a mandatory pre-write step.
- The `application`-avoidance approach (build capability/storage snapshots
  directly from `device.info.DeviceInfo` rather than importing
  `iopenpod.application.services`).
- The podcast integration path (`PodcastEpisode`/`PodcastFeed` built
  directly from `podcast-manager`'s state DB, `build_podcast_sync_plan`,
  merged into the main plan) — no file tagging needed, ever.
- The `FingerprintCache.get_instance().save()` workaround, until/unless
  it's fixed upstream.
- Real progress callbacks (`BackupProgress`/`EngineProgress`/`SyncProgress`)
  — the spike ran silently for 50+ minutes at a time; see `notes.md`.
- The device-capability monkeypatch workaround for iPod Video, until/unless
  it's fixed upstream or this device's real native artwork format is
  determined and added to a local override table.
- The play-status read-back path (`match_ipod_tracks`/
  `_update_episode_playback_from_track`) as the starting point for M8's
  Pocket Casts round trip.
