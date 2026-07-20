# Notes / Future Work

## M8: podcast play-status round trip — shipped (status confirmed, position not)

M8's acceptance criterion: *"Episodes played on-device are correctly
marked played in Pocket Casts after next sync."* Scoped to podcast
play-status only — the related 5-star-rating→favourite/like expansion
(logged separately below) touches three none-yet-built platform "like"
APIs and is real follow-on work, not part of this milestone.

**Read side** (`sync_orchestrator/playstate.py`, wired into
`sync.py`'s `plan_sync`): iOpenPod's `load_ipod_library()` already
parses the device's `Play Counts` file and merges deltas
(`recent_playcount`, `bookmark_time`, `rating`) into every track dict on
every call — confirmed read-only (never deletes/modifies the file), so
this runs on a plan-only pass, no `--execute` needed, decoupling "push
real listening progress back" from "sync new content forward." Device
track → Pocket Casts episode correlation goes through iOpenPod's own
`sync/mapping.py` `MappingFile` (`get_by_db_track_id` →
`source_path_hint`, a PC file path) matched against
`state/{profile}.sqlite`'s `episodes.local_path`. `resolve_played_states`
only calls an episode "played" once its bookmark position reaches ~90%
of a *known* duration — not just `recent_playcount > 0` alone — a
deliberate improvement on the still-open "already-listened episodes
redownload" bug below: this gives our own reliable on-device signal
instead of depending solely on Pocket Casts' own `EpisodeState` rows.

**Write side** (`podcast_manager/api.py`'s new `update_episode_status`,
new `podcast-manager push-play-status` CLI command): kept as a
*separate* step from `sync-orchestrator`'s read side — mirrors the
existing precedent (`sync.py`'s `_load_podcast_feeds` already reads
`podcast-manager`'s state db directly via raw `sqlite3` rather than
importing it as a Python package) so `sync-orchestrator` doesn't gain
`httpx`+Pocket Casts API logic just for this. New `episodes.pending_push`
column is the handoff: `sync-orchestrator` sets it on a real local
change, `podcast-manager push-play-status` clears it after a successful
push.

**Live-verified against the real account, with a genuine before/after
state transition** (not just re-sending an already-matching value,
which would silently pass even if broken — a mistake caught mid-testing
here: two early "confirmations" turned out to be no-op re-pushes of
already-current values):
- `status` (played/unplayed/in-progress) — confirmed: pushed `played=True`
  to a real episode that was genuinely `played=False`, re-fetched via
  `list_episode_states`, confirmed it flipped. Reliable.
- `played_up_to` (resume position) — confirmed **not** reliable: pushed
  a new position (`5`, then `42` with the camelCase field name
  `playedUpTo`) to an episode previously at `0`; both requests returned
  `200 OK` with no error, but the position silently stayed `0` both
  times. The real iOS app's sync protocol uses Protocol Buffers in
  places (confirmed via the open-source `Automattic/pocket-casts-ios`
  client) — position sync specifically may need that instead of this
  simple JSON endpoint. `played_up_to` is still sent (harmless, and
  future-proofs for if it starts working) but not relied on.
- Test episode (`Linux Matters` / "Clearing the Decks") restored to its
  real original state (`played=False, played_up_to=0`) both remotely
  and in local state.sqlite after testing — no lasting change to the
  real account from this investigation.

**Status**: done for the milestone's actual acceptance criterion
(played/unplayed marking) — verified end-to-end via the real
`update_episode_status` write path. Precise resume-position sync is a
known, separate gap; revisit only if reverse-engineering the protobuf
sync protocol becomes worth it. Full device-read-back round trip
(`sync-orchestrator sync` → `state.sqlite` picks up `pending_push` →
`podcast-manager push-play-status` clears it) has solid unit coverage
(`test_playstate.py`, `test_state.py`) but the device-dependent half
wasn't live-tested this session — the iPod wasn't connected at the
time; the write-path CLI was still verified fully via a manually-seeded
pending row. Verify the true end-to-end device flow next time the
device is connected.

## fetcher-ytmusic: built, metadata works, downloads blocked on YouTube's PO Token gate (re-shelved)

Built `services/fetcher-ytmusic/` from scratch (M3's other fetcher — was
previously just a placeholder Dockerfile). Mirrors `fetcher-apple`'s
contract and structure closely: `api.py` (`list_playlists`/
`get_playlist_tracks` via `ytmusicapi`), `tag.py` (same MP4 freeform
dedup-tag convention, since output is `.m4a`), `download.py` (per-track
fetch via `yt-dlp`, same shape as `fetcher_apple`'s/`fetcher_spotify`'s
own per-track fallback paths — YouTube has no whole-playlist-in-one-shot
shortcut like gamdl's `--save-playlist`), `cli.py`.

**Metadata layer fully verified live**, no auth needed: `ytmusicapi`
1.12.1's `get_playlist()` works completely unauthenticated against a
real public playlist (`john.yaml`'s "Semaphore" entry,
`PLLtbEg-839W9x0GthAoFP7ZP1w0_1f4jV`) — 31 available tracks resolved
correctly (title/artist/album/videoId). `get_library_playlists()` (for
`list_playlists()`, the account's own library) does need OAuth, and per
the library's Nov 2024 change that requires a self-registered Google
Cloud OAuth client — same shape of problem as the Spotify client_id
saga, not yet set up, but not a blocker for the fetch path.

**Real blocker (not a code issue), confirmed with `--list-formats`**:
downloading via `yt-dlp` — even with a real, freshly-exported YouTube
cookies file (`config/secrets/youtube_cookies.txt`, Netscape format) —
returns *zero* usable audio formats for every track, only thumbnail
storyboards. Two YouTube-side anti-bot mechanisms are gating it
simultaneously:
1. Signature/"n challenge" solving requires a JS runtime (`deno`/
   `node`) — not installed, and not something this session can install
   (no passwordless sudo).
2. Even with that, the `web_music` client's HTTPS formats require a
   **GVS PO Token**, a newer, separate mechanism. The standard fix
   (`bgutil-ytdlp-pot-provider`) isn't a simple pip install — it
   requires a **separate, persistently-running companion service**
   (Docker or a Node/Deno process) that mints tokens on demand.
   Architecturally the same category of thing as gamdl's optional
   wrapper for lossless Apple Music, which was already deliberately
   skipped for the same reason: real extra infrastructure for a
   secondary capability.

Ruled out the cheap alternatives before concluding this: `--cookies-
from-browser firefox` (both the default profile and explicitly
`5cofd3no.default-release`) performed *worse* than the exported
cookies.txt — fails at the earlier basic bot-check instead of reaching
format resolution, meaning the local Firefox session is less
authenticated than the manual export. Forcing a different extractor
client (`--extractor-args "youtube:player_client=android"`) also didn't
help — `android` gets skipped entirely once cookies are present. Already
on the latest yt-dlp (2026.7.4, confirmed against PyPI), so this isn't a
stale-version problem — it's the current state of the yt-dlp/YouTube
arms race.

**Status**: re-shelved (same operational decision as fetcher-spotify's
Premium block), for a precisely known, entirely platform-side reason.
Code is complete and correct — full unit test coverage (mocked
subprocess, same pattern as the other fetchers), live-verified metadata
resolution. Revisit only if: YouTube loosens the PO Token requirement,
or setting up the `bgutil-ytdlp-pot-provider` companion service (likely
as its own `docker-compose.yml` entry, matching how `sync-orchestrator`
is the one service that can't be containerized rather than everything
being forced into one shape) becomes worth the added infrastructure.

## gamdl: upstream fix for Apple "Mix" playlist URL support

gamdl's CLI can't parse Apple Music's personalized/algorithmic "Mix"
playlists (Chill, New Music, etc.) — their catalog id uses a `pl.pm-*`
prefix that doesn't match either shape gamdl's `VALID_URL_PATTERN` regex
accepts (`pl.[0-9a-z]{32}` or `pl.u-[a-zA-Z0-9]+`), in
`gamdl/interface/constants.py`.

Confirmed live (2026-07-18):
- Direct regex test against the installed pattern:
  `pl.pm-20e9f373919da080f80c0eceb6aae553` does not match.
- Tried stripping the `pm-` prefix down to a plain `pl.<32-hex>` id (which
  *does* match) — Apple's own catalog API returned a clean 404 for it. The
  `pm-` prefix is a real, required part of the identifier, not a
  formatting quirk.
- The downstream code path already works: gamdl's own
  `AppleMusicApi.get_playlist()` (the catalog endpoint) resolves
  `pl.pm-*` playlist ids fine when called directly — confirmed via
  `fetcher_apple.api.get_playlist_tracks()`, which uses that exact same
  call and successfully returned real track data for "Chill". The regex
  is a pure CLI-level gate before any of that ever runs.

**Fix idea**: widen the playlist-id alternation in `VALID_URL_PATTERN` to
also accept `pl.pm-[a-zA-Z0-9]+` (or a broader `pl.`-prefixed catch-all, in
case Apple has other undocumented Mix-type prefixes). Looks low-risk given
the downstream handling already works — worth upstreaming as a PR to
https://github.com/glomatico/gamdl.

**Status**: not started, planned for later. Our own `fetcher-apple`
service works around this today with a per-track download fallback (see
`services/fetcher-apple/src/fetcher_apple/download.py`), so this isn't
blocking anything — just worth doing upstream eventually so playlist-based
downloads for these playlists become as efficient as normal ones, and the
fallback path stops being needed.

## sync-orchestrator: real progress reporting — shipped

The M6 spike script (`services/ipod-sync/spike/headless_write_poc.py`)
called `BackupManager.create_backup()` and `SyncEngine().run(...)`
without passing a `progress_callback`, even though both accept one
(`BackupProgress`/`EngineProgress` respectively). This made the spike run
opaque for long stretches — the first full-device backup took ~30+ min
over USB with zero output in between, and the only way to see it was
happening was polling the backup directory's file count/size from
outside the process.

**Shipped**: `plan_sync`/`execute_sync`
(`services/sync-orchestrator/src/sync_orchestrator/sync.py`) now accept
an optional `progress_callback: Callable[[str], None]`, wired through to
both `BackupManager.create_backup()` and every `EngineRequest`. `cli.py`
passes a plain `print`-based sink, so a real run now shows e.g. `[scan]
3120/4416 — Talking Heads/...m4a` instead of going silent.

One thing found while wiring this up: iOpenPod's own progress callbacks
(`pc_library.py`'s scan loop, confirmed by reading it) fire completely
unthrottled — once per file, no batching. For a ~4,400-file external
library plus a large device, printing every callback verbatim would be
thousands of lines of spam. Added `_ThrottledProgressPrinter` in
`sync.py`: at most one line per second per stage, but always prints on a
stage change or on completion (`current >= total`) so nothing important
gets swallowed by the throttle.

**Status**: done, shipped 2026-07-20.

## iopenpod: device-side fingerprint cache is never persisted to disk (worked around)

Root-caused 2026-07-19 (originally just noted as "doesn't persist" —
now confirmed exactly why). `sync/audio_fingerprint.py`'s
`FingerprintCache` is a real, working, disk-backed singleton
(`~/.cache/iOpenPod/fingerprint_cache.json`, keyed by path+mtime+size) —
inspecting the file directly confirmed 4,960 real entries, all correctly
hit on repeat PC-side scans (`4959/4960 cache hits, 0 computed`). But
**zero entries were for the iPod**, despite the device-side fingerprinting
code path (`fingerprint_diff_engine.py`'s `_ipod_track_fingerprint_index`
→ `get_or_compute_fingerprint_with_status`) calling `cache.store()`
correctly for every device track, same as the PC-side path.

The reason: `FingerprintCache.save()` (writes the in-memory dict to disk)
is only ever called right after the **PC-side** library scan finishes —
grepped the whole file, there is no matching call anywhere after
`_ipod_track_fingerprint_index()`, which runs later in the same
`compute_diff()`. Device-side entries genuinely get stored in memory the
whole time; they're just discarded when the process exits instead of
being flushed. Confirmed by three separate PLAN runs against the same
device, each in its own process, all re-fingerprinting all ~4559
on-device tracks from scratch over USB (~50-55 min each), with 0 cache
hits on that side every time — this fully explains that earlier
observation.

**Workaround used** (`services/ipod-sync/spike/headless_write_poc.py`):
call `FingerprintCache.get_instance().save()` ourselves right after
`SyncEngine().run(EngineRequest(operation=PLAN, ...))` returns, forcing a
flush of whatever accumulated in memory (both PC and device side) by that
point. Since the cache is a real singleton keyed off a stable disk path
independent of our process, every run *after* this fix should see genuine
device-side cache hits, eliminating the ~50-minute cost for repeat syncs.

**Fix idea (upstream)**: add a `FingerprintCache.get_instance().save()`
call after `_ipod_track_fingerprint_index()` completes (or at the end of
`compute_diff()` generally) so this doesn't require a caller-side
workaround. Worth filing alongside the other iopenpod findings at
https://github.com/TheRealSavi/iOpenPod.

**Confirmed live (2026-07-20)**: the sync-orchestrator execute run right
after the two preceding plan-only runs (both of which paid the full
device-side fingerprinting cost) came back fast — the persisted cache
from those earlier runs meant this one hit cache instead of
re-fingerprinting the whole device again. The workaround holds up under
real, repeated use, not just the original one-off M6 test.

**Status**: worked around locally, now verified across multiple real
runs against the same device. Matters a lot for M9: a periodic
cron-triggered sync needs the device side to be cheap on repeat runs, not
just the PC side, or every sync against a large library pays close to an
hour of USB-bound fingerprinting regardless of how little actually
changed — confirmed this is no longer the case once the cache is warm.

## iopenpod (PyPI `iopenpod==1.66.2`): incomplete device support for 5th/5.5th-gen "iPod Video"

Confirmed live against a real device (`lsusb`: "ID 05ac:1209 Apple, Inc.
iPod Video"; on-device `SysInfo`: `ModelFamily: iPod Video`) — two
distinct bugs/gaps, found while getting the M6 headless PoC to actually
write to this hardware:

1. **`device/models.py`'s model tables have no "iPod Video" entries at
   all** — only 6th/6.5th/7th-gen "iPod Classic" (2007-2009) are fully
   modeled with `model_number`/capabilities/cover-art-format data. USB PID
   `0x1209` (shared by 5th and 5.5th gen) is deliberately mapped to a
   coarse `("iPod", "")` placeholder in `USB_PID_TO_MODEL`, explicitly
   commented "5th/5.5th Gen share this coarse PID" — clearly meant to be
   disambiguated by a more specific source (SysInfo, serial lookup), not
   used as a final answer.

2. **`device/info.py`'s `_restore_usb_pid_identity_if_needed()` discards a
   more specific, correct cached identity in favor of that coarse
   placeholder.** Our device's own `SysInfo` correctly said
   `ModelFamily: iPod Video`, but `enrich()` logged `cached family 'iPod
   Video' conflicts with live USB PID 0x1209 family 'iPod'; using live USB
   identity` and overwrote it with the generic `"iPod"` — i.e. it prefers
   the *coarser* of two identities whenever they textually differ, with no
   check for which one is actually more specific.

Downstream effect: `DeviceCapabilities` defaults `supports_artwork=True`
even for this unrecognized family (with `cover_art_formats=()`), so
`write_itunesdb` unconditionally attempts an ArtworkDB write and correctly
aborts rather than guess a format — a good defensive default, but it means
this device generation cannot sync via this iopenpod version at all
without a workaround. Worth noting too: `EngineRequest.device_capabilities`
is **not** what controls this — `iopenpod.sync._db_io.write_database`
ignores it entirely and re-resolves capabilities itself via
`iopenpod.device.get_current_device_for_path()` (a private in-process
registry, `iopenpod.device.info._Store`) and `capabilities_for_family_gen()`
(the same static, incomplete table) — confirmed by reading both functions
after passing `supports_artwork=False` through `EngineRequest` had no
effect on a real run.

**Workaround used** (`services/ipod-sync/spike/headless_write_poc.py`):
monkeypatch `iopenpod.device.get_current_device_for_path` and
`iopenpod.device.capabilities_for_family_gen` for the duration of the
script, forcing a `DeviceCapabilities(supports_artwork=False, ...)`.
`mhbd_writer.write_itunesdb` already has a graceful fallback for exactly
this case (writes a generic 320x320 "iOpenPod-only view" format instead of
the native device format) — it just needs `supports_artwork=False` to
actually reach it, which the private-registry re-resolution prevents by
default.

**Fix idea (upstream)**: add real `device/models.py` entries for 5th/5.5th
gen iPod Video (model numbers, capacities, and — ideally — real native
cover-art format specs, not just the existing iOpenPod-only fallback), and
make `_restore_usb_pid_identity_if_needed()` prefer a specific cached
identity over a placeholder/coarse PID-derived one instead of the reverse.
Worth filing as an issue/PR against https://github.com/TheRealSavi/iOpenPod.

**Status**: not started, worked around locally for M6. All 400 new tracks
on the real device currently have iOpenPod-fallback-format artwork rather
than native-format artwork as a result — acceptable for now, revisit if
native artwork on this device generation matters later.

## podcast-manager: Pocket Casts credentials need reversible encryption before production

Pocket Casts credentials should not be stored in plaintext in production.
Currently `config/secrets/pocketcasts/{profile}.json` holds the
email/password in plaintext, which is fine for local dev but not for a
real deployment. Needs reversible (bi-directional) encryption — not a
one-way hash, since podcast-manager needs the actual plaintext password
to authenticate against Pocket Casts' API — so something like a symmetric
encryption scheme (e.g., encrypted at rest with a key from the OS
keyring, a `.env`-supplied master key, or a secrets manager) that
decrypts only in memory when needed.

**Status**: not started. This is a production-hardening item, likely
relevant around M10 ("hardening") in the planning doc, or worth doing
whenever podcast-manager's credential loading is touched next.

## Future: audiobook acquisition and integration

Investigate ways to acquire audiobook files and integrate them into
music-stack at a later stage. iOpenPod itself already has some audiobook
awareness worth reusing — `sync/pc_library.py`'s `PCTrack` docstring
explicitly covers "audio, video, podcast, or audiobook" content, and it
has real audiobook detection logic (distinct from podcast detection).
No acquisition source has been picked yet (this is a new content type,
not just a new source for existing music/podcast pipelines — unclear yet
whether it fits the existing fetcher-* pattern or needs its own service).

**Status**: not started, purely a future idea — no design work done yet.

## fetcher-apple: per-track fallback wrote relative paths into .m3u8 (fixed)

Confirmed live during the M6 full-device sync: iOpenPod's playlist-file
sync (`sync/sync_playlist_files.py`) skipped nearly every entry in two
playlists — "Chill" (25/25 skipped) and "New Music" (20/21 skipped) —
while five other playlists synced perfectly. Root cause:
`fetcher_apple.download.fetch_playlist`'s two internal code paths handled
path absoluteness inconsistently. `_fetch_via_playlist_url` always
produces absolute paths (`.resolve()` on gamdl's own `.m3u` output), but
`_fetch_per_track` (the fallback used for `pl.pm-*` "Mix" playlists —
Chill and New Music both needed it) built paths directly from
`library_root` with no resolution step. The CLI was invoking
`fetch_playlist` with a relative `--library-root library/music`, so only
playlists that happened to go through the fallback path ended up with
relative paths in their `.m3u8` — and iOpenPod's playlist scanner
couldn't resolve them against the PC folder it was scanning.

**Fix**: `fetch_playlist` now calls `.resolve()` on `library_root` itself
at the top, so both code paths are guaranteed absolute regardless of what
the caller passes in. Fixed 2026-07-19, regression test added
(`test_fetch_per_track_m3u8_paths_are_absolute_even_with_relative_library_root`).
The two already-broken `.m3u8` files were repaired in place (rewritten
with absolute paths, no re-download needed — the audio files were already
correct on disk).

**Status**: done.

## podcast-manager: episode download retry with backoff (done)

Investigated the 6 episode download failures from the first full podcast
sync (2026-07-19). Not a single bad show/host: 6 episodes across 3
unrelated CDN hosts (megaphone.fm, podtrac.com, podbean.com), all
ReadTimeout/RemoteProtocolError partway through — and every one was a
30-90 minute episode, the longest in its respective show. Conclusion:
transient network drops that are simply more likely to hit a long
streaming download somewhere along the way, not a code or host bug.

**Fix**: `_download_enclosure` now retries up to 3 times with linear
backoff (5s, 10s) before giving up, so most of these clear automatically
within one `sync_podcast()` call instead of needing a manual re-run.
Still ultimately reports to `SyncResult.failed` if all retries are
exhausted. Regression tests added
(`test_download_enclosure_retries_and_succeeds_on_later_attempt`,
`test_download_enclosure_raises_after_exhausting_all_retries`).

**Status**: done.

## Per-podcast listening order (newest-first vs. chronological) — done

Some podcasts should sync "get me the latest unlistened episode" (news,
commentary shows), but others make more sense listened to in
chronological order from wherever you left off (serialized fiction,
courses, anything where episode order matters).

Originally assumed iOpenPod's own `podcasts.models.PodcastFeed.fill_mode`
("newest"/"next") was the thing to wire up — it is not: read
`podcast_sync.py` closely and confirmed `fill_mode`/`episode_slots` are
only ever consulted inside `build_podcast_managed_plan` (and its
`_plan_newest_mode`/`_plan_next_mode` helpers), a heavier function that
also handles auto-removal to fit a fixed device slot count. The function
this project actually uses, `build_podcast_sync_plan`, never reads
`fill_mode` at all — setting it on the `PodcastFeed` objects
`sync_orchestrator` builds would have been a silent no-op.

The real fix belongs one layer up, in `podcast-manager`'s own episode
*selection* (`download.py`'s `sync_podcast()`), which is what actually
decides which episodes get downloaded in the first place — this project
doesn't use iOpenPod's own subscription/slot management at all, so that's
the only place this ordering can matter.

**Implemented**: `ProfilePodcastsConfig.fill_modes: dict[str, "newest" |
"next"]`, keyed by podcast UUID (same convention `shows` already uses).
`sync_podcast()` gained a `fill_mode` parameter — `"newest"` (default,
unchanged behavior) sorts newest-first; `"next"` sorts oldest-first
among unplayed episodes instead, so a fixed `max_episodes_per_show`
resumes chronologically rather than always grabbing the latest. Wired
through `cli.py`'s `_cmd_sync` loop. New test
(`test_sync_podcast_next_fill_mode_picks_oldest_unplayed`), example added
to `bob.yaml`.

**Status**: done, 2026-07-20.

## Bug to investigate: some already-listened episodes get downloaded anyway

Observed live 2026-07-19: after the combined sync, some episodes that had
already been listened to were still downloaded. `sync_podcast()`'s
`sync_unplayed_only` filter relies on `list_episode_states()` having a row
for that episode — and we already know from M5
(`podcast_manager/api.py`'s `EpisodeState` docstring) that Pocket Casts
"only returns a row here for episodes the user has actually interacted
with... there is no row at all for an episode still in its default/
untouched (unplayed) state." If a real listen doesn't reliably produce
that row (sync lag between devices, a threshold not met, listened to via
a different app, etc.), a genuinely-played episode would incorrectly be
treated as unplayed and downloaded again.

**Fix idea**: instrument/log the actual `EpisodeState` rows Pocket Casts
returns for a few episodes the user knows they've listened to, to confirm
whether this is a Pocket Casts API data gap, a sync-timing issue, or a bug
in how we match states by UUID.

**Status**: not started, needs live investigation.

## Future: absolute vs. additive playlist sync

Some playlists should be "absolute" — always mirror exactly what the
source (Apple Music/Spotify/YouTube) currently has, including removals.
Others should be "additive" — only ever add new tracks locally, never
remove, since some source playlists (especially platform-curated ones
like Apple's algorithmic Mixes) rotate/shrink their contents to stay a
fixed length, and losing tracks locally just because the platform rotated
them out isn't wanted.

Unlike the podcast fill_mode case, this doesn't need any new iOpenPod
capability — iOpenPod's playlist-file sync (`sync_playlist_files.py`) just
mirrors whatever `.m3u8` file it's given. "Additive" mode can be
implemented entirely at our own layer: before a fetcher overwrites a
playlist's `.m3u8` (`common/playlist.py`'s `write_m3u8`), read the
existing file's current entries and union them with the newly-fetched
list instead of replacing it outright, for playlists configured as
additive. "Absolute" playlists keep today's replace-outright behavior.

**Fix idea**: add a `sync_mode: absolute | additive` (or similar) field to
each playlist entry in the profile YAML's `playlists` list, and branch on
it in each fetcher's `fetch_playlist` before calling `write_m3u8`.

**Status**: done (2026-07-20). `PlaylistEntry.sync_mode` (default
`"absolute"`) added to `common/models.py`; `write_m3u8` gained a `mode`
parameter — `"additive"` reads the existing `.m3u8`'s entries first and
unions in new ones by exact string match, never dropping anything already
there. Wired through both `fetcher-apple` and `fetcher-spotify`'s
`fetch_playlist`/`cli.py`. Applied to the real profile: "Chill" and "New
Music" (both genuinely Apple algorithmic Mixes) are now `additive` in
`config/profiles/john.yaml`; `alice.yaml` updated as a worked example
too. 6 new tests in `test_playlist.py`, full suite (105 across root +
`fetcher-spotify` + `sync-orchestrator`) still green.

## M8 scope expansion: 5-star rating -> "favourite"/"like" on the source platform

A track rated 5 stars on the iPod should get marked as a favourite/like on
whichever platform is its "main source" (Apple Music, Spotify, YouTube
Music) — not just have the rating recorded locally. Extends M8's
play-status round trip (already scoped for play counts/played-position →
Pocket Casts for podcasts) to ratings → source-platform favourites for
music.

Needs: (1) reading the on-device rating back per track (iTunesDB track
dicts already carry a rating field, same general mechanism as the
play_count_1/last_played fields M8's podcast round trip already reads —
see the `iopenpod` podcast round-trip section above), (2) resolving each
track's "main source" (source + source_id are already tagged per track by
every fetcher for dedup, per the fetcher output contract in CLAUDE.md —
after cross-source dedup picks a canonical version, that's the main
source), (3) a per-source "mark as favourite/liked" API call — Apple
Music's library API, Spotify's "Save Track"/Liked Songs, YouTube Music's
like endpoint — none of which exist in any fetcher yet.

**Status**: not started, noted 2026-07-19 as an M8 scope expansion.

## fetcher-spotify: migrated to an actively-maintained zotify fork — auth fixed, but blocked on Premium (re-shelved)

Revisited the M3 shelving decision (2026-07-19). Root-caused the original
403 `MercuryException` precisely this time: Spotify deprecated the old
"keymaster" Web API token method industry-wide in August 2025 in favor of
"login5". `zotify-dev/zotify` — both `main` and the `v1.0-dev` branch we
were pinned to — never got the fix; the `v1.0-dev` branch hasn't been
touched since September 2024, and its own `Pipfile.lock` still pins
`librespot` to a June 2024 commit that predates both the breaking change
and its fix. Effectively abandoned on this specific issue.

**Migrated to `Googolplexed0/zotify`**, an actively maintained fork (526
stars, commits through June 2026, created explicitly because the original
went stale) that carries the login5 fix in its own `librespot` fork.
Pinned to specific tested commits (not tracking `main`), per this
project's usual fetcher-dependency discipline:
- `zotify @ git+https://github.com/Googolplexed0/zotify.git@9ea3210198e1ad9f3fc995cca046973ff77238e5`
- `librespot @ git+https://github.com/Googolplexed0/librespot-python.git@7a89401ba151897d04efc6e8476c8ed68d417b3e`

Code changes needed, both confirmed necessary by reading the fork's own
`zotify/config.py` `Zotify.login()` logic:
- `fetcher_spotify/api.py`: credentials saved via interactive login can
  now be either the legacy raw stored-credentials blob (loaded via
  `Session.Builder.stored_file()`, unchanged) or a new OAuth PKCE JSON
  format (`{client_id, access_token, refresh_token, expires_at, type:
  "OAUTH_PKCE_TOKEN"}`) when a custom `--client-id` is used — `_build_session()`
  now branches on `creds["type"]` and reconstructs an `OAuth` object for
  the PKCE case, mirroring the fork's own login branching exactly.
- `fetcher_spotify/download.py`: CLI flags changed — `--credentials` →
  `--creds`, `--album-library` → `--root-path`, `--audio-format` →
  `--codec`. `tag.py` (pure mutagen ID3 tagging) needed no changes at all.
- `session.tokens().get_token(*scopes)` (used for our own Web API calls)
  keeps the exact same public signature — confirmed by reading the
  installed `TokenProvider.get_token()` source directly: it now calls
  `self.login5(scopes)` internally instead of the old keymaster path, so
  no caller-side change was needed there.

**Confirmed live that the actual auth fix works**: a fresh interactive
OAuth login (browser-based PKCE flow, zotify's `--creds`/`--client-id`
flags) produced a real, valid session — proven by getting a **429 Too Many
Requests** on `api.spotify.com/v1/me/playlists` instead of the old 403.
That's a fundamentally different, far more benign class of error: the
login5 auth genuinely succeeded; something else was rate-limiting us.

**Real blocker found (not a code issue)**: registered a private Spotify
Developer app (client_id `d38e5c1b8594498a8ce0c73494d5cabc`, redirect URI
`http://127.0.0.1:4381/login`, "Web API" scope) to rule out the shared
default client_id being globally rate-limited by other zotify users — the
429 persisted identically even on a brand-new, never-used client_id,
ruling that theory out. Then, testing zotify's own internal metadata
resolution directly (bypassing our own Web API calls entirely) on both a
playlist and a single track produced a deterministic, non-rate-limit
error: `"ATTEMPTING TO ACCESS FORBIDDEN ENDPOINT"` /
`"Active premium subscription required for the owner of the app."`
Confirmed on two different endpoint types (playlist metadata, single-track
metadata) — this is a hard Spotify account-tier restriction, not
something fixable in code. The account in question is Spotify Free.

**Status**: re-shelved (same operational decision as the original M3
shelving), but for a completely different and now precisely known reason.
The migration itself is done and correct — pinned to known-good commits,
all 10 tests passing, code changes mirror the fork's own logic exactly.
No further migration work is needed; this should just work the moment the
account has an active Premium subscription. The registered developer app
(client_id above) and the real OAuth credentials obtained during testing
are still in place locally (`config/secrets/spotify_credentials.json`,
gitignored) for whenever that happens.

## library-manager's dedup doesn't scan MusicLibrary — but iOpenPod's own sync-time dedup does

Investigated 2026-07-19 after a real concern: does anything catch a track
that's newly downloaded via a fetcher but already exists in the separate,
pre-existing `~/Music/MusicLibrary`? Two findings:

1. **`library-manager dedup` only scans one `--library-root`** (confirmed
   by reading `cli.py`: a single required arg, passed to `scan_library()`)
   — it has no awareness of `MusicLibrary` at all. A track fetched fresh
   into `music-stack/library/music` that duplicates something already in
   `MusicLibrary` is invisible to this dedup pass entirely.

2. **iOpenPod's own device-sync `FingerprintDiffEngine` already covers
   this at sync time**, independent of (1) — confirmed by reading
   `fingerprint_diff_engine.py`'s "Phase 2: Group by identity" step. It
   fingerprints every file across *all* `pc_folders` given to PLAN
   combined (in our case, `MusicLibrary` + `music-stack/library/music` +
   the playlists folder), groups by "same fingerprint + same album = true
   duplicate," keeps one canonical copy, and reports the rest via
   `plan.duplicates` rather than silently adding both to the device. This
   is genuinely acoustic-content-based (not filename/tag-based), so it
   catches duplicates even with different encodes/filenames.
   `headless_write_poc.py` never printed `plan.duplicates` — fixed, now
   surfaced in the plan output.

These aren't fully redundant, though: iOpenPod's check requires matching
*album* tags to call something a true duplicate (by design — "same
fingerprint + different album" is treated as legitimately independent,
e.g. a greatest-hits re-release). `library-manager`'s own dedup uses
ISRC + fuzzy artist/title matching, no album requirement, so it could
catch same-song-different-album-tag cases iOpenPod's stricter check
would miss. And even where iOpenPod does catch it, an un-deduped local
copy in `music-stack/library/music` still wastes local disk space and
clutters playlist files, even though it won't reach the device twice.

**Fix idea**: expand `library-manager dedup` to optionally accept
additional read-only "reference" library roots (like `MusicLibrary`) to
compare against, without trying to manage/quarantine files outside its
own `--library-root` (those aren't ours to move).

**Status**: `plan.duplicates` surfacing fixed. The `library-manager`
scope expansion is not started, noted 2026-07-19. Live-checked overlap
between the two libraries by normalized title+artist for the tracks
synced so far and found zero — but this doesn't cover playlists not yet
fetched (e.g. the two ex-Spotify playlists pending Apple Music
migration), which is what prompted this investigation.

## Selective sync from an external library — shipped (`external_library` config)

Follow-up to the note below this one, from when M7 started: the ability
to choose specific artists/albums/songs to sync from a personal library
that lives outside music-stack's own managed `library/` folder (e.g.
`~/Music/MusicLibrary`), instead of mirroring the whole thing.

**`EngineOptions.allowed_paths` turned out to be unsafe for this** — it
was the obvious-looking mechanism (see the original note below), but
tracing it through `iopenpod/sync/planning_stages.py`
(`scan_source_libraries`) and `iopenpod/sync/fingerprint_diff_engine.py`
(`_plan_removed_tracks` → `_plan_orphaned_mapping_removals`) showed it
narrows *Phase 1 PC-side scanning*, which shrinks `seen_fps`. Removal
planning then computes `orphaned_fps = mapping.all_fingerprints() -
seen_fps` — any previously-synced track whose fingerprint isn't in this
run's (now narrower) scan gets treated as "removed from PC" and staged
for device removal, regardless of whether the file is still on disk.
Used directly for "sync just this subset," it would have proposed
deleting every previously-synced track outside that subset.

**Design used instead** (`services/sync-orchestrator/src/
sync_orchestrator/selection.py`): resolve the profile's
`external_library.selections` (artist/album/track path-prefix matches,
`mode: include` = whitelist or `mode: exclude` = blacklist) into a
staging directory of symlinks, fully rebuilt every run, and pass *that*
directory as a `pc_folder` instead of the raw library path. iopenpod
never sees the deselected files, so it can't reason about them — same
"build the safety guarantee at our own layer" approach already used for
additive/absolute playlist sync. Confirmed safe with `pc_library.py`'s
plain `os.walk` (no `followlinks=True`): it won't descend into a
symlinked *directory*, but a symlinked *file* inside a real directory is
read normally — staging only ever symlinks leaf files, never directories.

**Real, intended behavior change**: the first sync after narrowing a
selection proposes removing every previously-synced track that falls
outside it — expected (deselecting something should remove it from the
device), not a bug, but a one-time large batch the first time. The
existing hard safety gate (refuse `--execute` on any `to_remove`) was
loosened to require a second explicit flag, `--allow-removals`, passed
alongside `--execute` — `--execute` alone still refuses on any removal,
matching the original behavior for the too-narrow-`--pc-folder`-by-
accident case that gate was built for.

**Path validation added afterward**: `plan_sync` now checks
`external_library.path` itself exists before touching it, and a
`selections` entry that resolves to 0 files (near-certainly a typo'd
artist/album name) is printed as a warning at plan time but hard-blocks
`--execute` — never silently sync less (or, in `exclude` mode, more)
than the profile actually asked for.

**Nested selection shorthand added afterward**: a `selections` entry can
also be a single-key mapping of artist -> list of album/track names
relative to that artist, e.g. `"Talking Heads": ["Performance",
"Remixed"]` instead of repeating `"Talking Heads/Performance"`,
`"Talking Heads/Remixed"` as separate flat strings. Flattened into plain
strings by a pydantic `field_validator` on `ExternalLibraryConfig.
selections` (`services/common/src/common/models.py`) at config-load
time — `selection.py` and everything downstream only ever sees flat
strings, same as before. The two forms mix freely in one list.

**Status**: done, shipped 2026-07-20.

## M7 (sync-orchestrator) shipped: real device discovery + config-driven service

Promoted `services/ipod-sync` into `services/sync-orchestrator`
(`git mv`), replacing the M6 spike's hardcoded paths with real device
discovery and profile/CLI-driven config. Two real bugs found and fixed
while building it, both confirmed live:

1. **`global.yaml`'s `paths.library_root`/`paths.state_root` are
   Docker-container paths** (`/data/library`, `/data/state`, per
   `docker-compose.yml`'s volume mounts) — but `sync-orchestrator`
   always runs bare metal, where those paths don't exist. Fixed by
   taking `--library-root`/`--state-root` as explicit CLI args instead,
   matching the pattern `fetcher-apple`/`podcast-manager` already use,
   rather than inventing a new, inconsistent way to resolve paths for
   the one service that can't use `global.yaml`'s values directly.
2. **`Path.is_file()` raises `PermissionError` instead of returning
   `False`** for a mount the current user can't read (`/boot/efi`,
   confirmed live) — device discovery's `is_ipod_mount()` was scanning
   *all* mounted vfat/hfsplus volumes and crashed the whole scan on this
   one unrelated, inaccessible mount. Fixed by catching `OSError` there
   and treating "can't even read it" as "not an iPod."

Also confirmed the real FAT volume label (via `lsblk -no LABEL`) differs
from the mount-point directory name — udisks2 sanitizes apostrophes
(`JOHN'S IPOD` on disk vs. `JOHN_S IPOD` as the actual mount path), so
`match_by: volume_label` has to read the label directly from the block
device, not infer it from the mount point.

Live-verified end to end against the real device and profile: correct
auto-discovery by `volume_label`, and a plan matching known-good numbers
(`to_add=0, to_remove=0`, all 7 playlists already in sync). This run also
surfaced 41 real cross-`pc_folder` duplicate groups (see the dedup
section above) for the first time since that reporting was added — all
correctly deduped by iopenpod, confirming that safety net actually works
on real data, not just in theory.

**Status**: M7 core done (`sync-orchestrator sync`, plan-only and
`--execute`). Device-level `FileLock` reused from the Apple Music session
lock work. Not yet done: M8 (play-status round trip), M9 (udev-triggered
automation — this service still assumes the device is already mounted).

## Workflow gotcha: standalone projects cache a stale `common` build

Hit three times now (`sync-orchestrator`, `fetcher-spotify`, then
`sync-orchestrator` again for the nested `external_library.selections`
mapping validator — added to `common/models.py` in one edit, but only
`uv sync --reinstall-package common`'d for the root workspace, not
`sync-orchestrator`'s own venv, so a real run failed with a pydantic
"Input should be a valid string" error against the *old* schema even
though the source was already correct): a standalone
`uv` project depending on `common` via `{ path = "../common" }` doesn't
automatically pick up changes to `common`'s source — it keeps using
whatever was built into its `.venv` at the last `uv sync`, even though
nothing about the dependency *declaration* changed. Symptom: `import`
succeeds but a newly-added function/parameter is missing
(`TypeError: unexpected keyword argument`) or a whole new module is
absent (`ModuleNotFoundError`), even though the source file clearly has
it. Root-caused as real staleness, not a bug in the new code, both times.

**Fix**: `uv sync --reinstall-package common` inside the standalone
project whenever `services/common` changes. Root-workspace members
(`fetcher-apple`, `podcast-manager`, `library-manager`) don't have this
problem — `{ workspace = true }` stays live automatically.

**Status**: known workaround, not really "fixable" — just something to
remember whenever `common` changes and a standalone project
(`fetcher-spotify`, `sync-orchestrator`) needs to see it.
