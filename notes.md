# Notes / Future Work

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

## sync-orchestrator (M7): wire up real progress reporting

The M6 spike script (`services/ipod-sync/spike/headless_write_poc.py`)
calls `BackupManager.create_backup()` and `SyncEngine().run(...)` without
passing a `progress_callback`, even though both accept one
(`BackupProgress`/`EngineProgress` respectively). This made the spike run
opaque for long stretches — the first full-device backup took ~30+ min
over USB with zero output in between, and the only way to see it was
happening was polling the backup directory's file count/size from
outside the process.

**Fix idea**: when M7 (the real sync-orchestrator service) is built, wire
these callbacks up properly from the start — print/log per-file or
per-stage progress (file count, bytes, current filename) as iOpenPod's
own `BackupProgress`/`EngineProgress`/`SyncProgress` objects already
carry that data. Don't repeat the M6 spike's "silent for 30 minutes"
behavior in anything meant for regular use.

**Status**: not started, noted during the M6 spike (2026-07-18) for when
M7 begins.

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

**Status**: worked around locally. Matters a lot for M7/M9: a periodic
cron-triggered sync needs the device side to be cheap on repeat runs, not
just the PC side, or every sync against a large library pays close to an
hour of USB-bound fingerprinting regardless of how little actually
changed. Should verify on the next real sync that device-side cache hits
actually show up.

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

## Future: per-podcast listening order (newest-first vs. chronological)

Some podcasts should sync "get me the latest unlistened episode" (news,
commentary shows), but others make more sense listened to in
chronological order from wherever you left off (serialized fiction,
courses, anything where episode order matters).

iOpenPod's own `podcasts.models.PodcastFeed` already has exactly this
concept built in — `fill_mode: str = "newest"`, with the alternative
`"next"` documented as "pick the next unheard episode (oldest on iPod + 1;
if none on iPod, pick the oldest retrieved episode)." We never set this
when constructing `PodcastFeed` objects in `headless_write_poc.py`'s
`_load_podcast_feeds()`, so every show currently defaults to `"newest"`.

**Fix idea**: expose this as a per-show setting in the profile YAML
(`podcasts.pocketcasts.shows` currently only takes a bare list of UUIDs or
`"all"` — would need to become a richer structure, or a separate
`fill_mode` map keyed by show UUID/name), and set
`PodcastFeed(fill_mode=...)` accordingly when building feeds for the sync
plan. No new iOpenPod capability needed — just wiring up what already
exists.

**Status**: not started, noted 2026-07-19 after confirming the M6
combined sync (music + playlists + podcasts) worked correctly end to end.

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

**Status**: not started, noted 2026-07-19.

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
