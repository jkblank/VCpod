# Notes / Future Work

## library-manager dedup had genuinely never been run — run for real, found a real duplicate

User noticed some songs on the device had multiple copies and asked to
check the dedup service. Root cause was simpler than a bug in the
dedup logic itself: `library/music/.duplicates/` didn't exist at all —
`library-manager dedup` had never actually been invoked once in this
project's real usage. Every fetch→sync cycle so far went straight from
fetching to `sync-orchestrator sync`, skipping the dedup step entirely
(the exact gap the "no sync-everything entrypoint" CLI-ergonomics note
above already flags — dedup is a real, separate manual command with its
own long flag list, easy to forget).

Ran it for real against the full library: scanned 1044 tagged tracks,
found and correctly quarantined 1 real cross-source duplicate (Hozier -
"Eat Your Young", kept the `apple_music` copy per `FIDELITY_ORDER`,
quarantined the `ytmusic` copy to `.duplicates/ytmusic/`) — and
confirmed both playlists that referenced the quarantined copy
(`Semaphore.m3u8`, `Songs to vape to.m3u8`) got correctly rewritten to
point at the canonical file.

**Related, separate finding surfaced while investigating**: 1044 scanned
vs. 1158 real `.m4a` files on disk — 114 files never got scanned at all,
because `scan_library()` silently skips anything missing our own
`source`/`source_id` dedup tags (by design — distinguishes "ours" from
a file a user dropped in manually). Traced these to real, legitimate-
looking tracks (clean title/artist tags, e.g. BETWEEN FRIENDS -
"Smiley", Bad Bunny - "DtMF") that `fetcher_apple`'s own fetch already
knew about and reported — `unmatched (downloaded but not tagged/
recorded): 109` and `: 7` were both printed during the "Songs to vape
to"/"Zanny twitch playlist" fetches earlier this session, but got missed
in the moment (not part of the summary grep pattern used to report
results back). `_fetch_via_playlist_url` only tags a downloaded file
once it's matched by title+artist against the playlist's own known
track list (`_match_track`) — these never matched anything, most likely
because gamdl's playlist download pulled in sibling tracks from the
same album/release rather than only the specific requested track (no
gamdl CLI flag found to prevent this). Since they're untagged, they're
invisible to dedup, unlinked from any playlist or state-db row, and
just sitting on disk — real wasted downloads, and potentially hiding
more duplicates dedup can't see.

**Fix idea**: (1) surface `unmatched` counts more prominently in fetch
output so they don't get missed again (or fail loudly above some
threshold rather than a single easy-to-miss summary line); (2) decide
what to do with the 114 already-orphaned files — delete, or try to
retroactively match/tag them; (3) investigate whether gamdl's whole-
album-download behavior can actually be constrained to just the
requested track, or if downstream matching needs to expect and handle
it better.

**Status**: dedup run for real, 1 real duplicate fixed. The
untagged/unmatched-files issue is newly discovered, not yet
investigated further or fixed — separate follow-up.

## fetcher-ytmusic: relative --library-root wrote unmatchable .m3u8 paths (fixed) — Semaphore synced empty

Discovered live 2026-07-21: the user checked the real device after the
big 757-track sync and "Semaphore" (the YouTube Music playlist) wasn't
actually there. The sync log's own `plan.playlists_to_add` entry for it
told the whole story even at PLAN time: `'_sync_playlist_total_entries':
31, '_sync_playlist_skipped_count': 31, 'items': []` — the playlist was
created on the device, but completely empty. Compare a working playlist
from the same sync ("Songs To Vape To"): `skipped_count: 0`.

**Root cause**: exact same bug class already found and fixed for
`fetcher-apple`'s per-track fallback path (see the "wrote relative paths
into .m3u8" entry below) — `fetcher_ytmusic/download.py`'s
`fetch_playlist()` never got the matching `library_root =
Path(library_root).resolve()` fix when it was built (mirrored the
overall structure but missed this one line). Invoked with a relative
`--library-root library/music` (as every real invocation this session
was), every path written into `Semaphore.m3u8` stayed relative
(`library/music/nimino/...`). iOpenPod's playlist-file matching compares
`.m3u8` entries against absolute paths from its own PC-folder scan, so a
relative path matches nothing — all 31 entries silently skipped, both
at plan time and (confirmed) at execute time. Fixed with the identical
one-line fix fetcher-apple already had.

**Real, already-synced damage needed a second fix**: the code fix alone
didn't retroactively repair anything — the 31 `tracks` rows in
`state.sqlite` already had the bad relative `local_path` stored from the
original fetch, and re-running `fetch_playlist()` reuses an
already-known track's stored `local_path` as-is (never re-resolves it).
Had to directly walk and fix all 31 rows via `StateDB.update_local_path`
(resolving each against the real repo root, verifying the file still
exists before rewriting) before regenerating a correct `Semaphore.m3u8`.
Regression test added
(`test_fetch_playlist_m3u8_paths_are_absolute_even_with_relative_library_root`,
mirrors fetcher-apple's own).

**Fix idea worth doing later**: this class of bug (a fetcher's stored
`local_path` silently going stale/wrong after a bug fix) can't be fully
protected against by a regression test on `fetch_playlist()` alone,
since the failure mode here was specifically in *already-recorded*
state, not a fresh run. Worth a light periodic health check across all
of `StateDB`'s `tracks`/`episodes` rows verifying `local_path` is
absolute and the file still exists, surfaced loudly rather than
discovered by the user checking their device.

**Status**: done, fixed 2026-07-21 — code + the real corrupted state
rows + a live re-sync verification still pending (device needs to be
reconnected to confirm "Semaphore" actually populates this time).

## CLI ergonomics — no "sync everything for this profile" entrypoint — shipped

Noticed live (2026-07-20): pulling all of a profile's playlists means one
`fetcher-apple fetch --playlist "X" --cookies-path ... --library-root ...
--playlists-root ... --state-path ...` invocation *per playlist*, repeating
the same four path flags every time. `john.yaml` alone has 10 playlists
across 2 sources (9 `apple_music` + 1 `ytmusic`) — 10 long, near-identical
command lines just to do a full pull, and that's before podcasts
(`podcast-manager sync`, its own separate long command) or a device sync
(`sync-orchestrator sync`, likewise).

The awkward part is that almost none of those flags actually vary per
playlist or need retyping — `library-root`/`playlists-root`/`state-path`
are the same for every playlist in a profile, and per-source credentials
paths are already fully determined by `global.yaml`'s
`sources.*.cookies_file`/`credentials_file`/`oauth_file`. Everything
needed is already derivable from just `--profile <path>` +
`--global-config <path>`.

**Fix idea, two levels, not mutually exclusive**:
1. Per-fetcher "sync all playlists for this profile" subcommand (e.g.
   `fetcher-apple sync-profile --profile ... --library-root ...
   --playlists-root ... --state-path ...`) that loops `profile.playlists`
   itself for that source, calling `fetch_playlist()` per entry — same
   internals as today, just removes the "one shell invocation per
   playlist" repetition.
2. A single top-level orchestrator (`music-stack sync --profile ...`?)
   that resolves *all* paths from `global.yaml`+the profile itself and
   drives every fetcher + `podcast-manager` + optionally
   `sync-orchestrator` in one command — the biggest ergonomics win, but
   a real new piece of surface area (would need to import each service's
   fetch functions directly rather than shelling out, or shell out to
   each service's own CLI with paths filled in on the caller's behalf).

**Status (2026-08-19): level 2 shipped, in two parts.**

`music-stack sync --profile <path>` (services/music-stack-cli) already
closed most of this gap before this entry was revisited — it resolves
every path from `--global-config`/the profile itself and loops every
playlist/show for a source in one call, with only `--profile` required.
`sync-orchestrator auto-sync` goes further, chaining fetch + device sync
+ play-status push automatically — but it's unattended-only (udev-
triggered, no profile choice, no `--playlist`/`--show`, always forces
`--execute --allow-removals`), the wrong tool for a human at a terminal.

Added `sync-orchestrator full-sync` as the interactive sibling: fetch +
device sync in one command, `--profile` accepts a bare name (e.g.
`john`, resolved via the new `common.config.resolve_profile_path`
against `--config-root/profiles/`) as well as a literal path,
`--library-root`/`--state-root` default next to `--config-root` the same
way `music-stack sync` already does, and `--source`/`--playlist`/`--show`
narrow the fetch stage for a quick partial check (`--fetch-only` skips
the device stage entirely, e.g. for a fetch-side-only sanity check).
`--execute`/`--allow-removals` deliberately stay plain opt-ins rather
than being forced on like `auto-sync` — this is an interactive command,
not the unattended "wake up synced" path, so the existing safety gate
(see the `docs/m6-ipod-headless-recommendation.md` near-miss) stays
intact.

Implementation reuses existing pieces rather than duplicating logic:
`_maybe_pre_fetch`'s subprocess command-building was factored out into
`_build_music_stack_sync_cmd` (now shared by `auto-sync`'s due-soon
pre-fetch, `full-sync`'s on-demand fetch, and `_maybe_push_play_status`);
the device stage calls the exact same `_run_sync` the plain `sync`
command uses, so play-status push-back after a real `--execute` comes
for free. 3 new tests in `services/common` (`resolve_profile_path`:
bare-name resolution, literal-path passthrough, unknown-name error
listing available profiles), 8 new tests in `sync-orchestrator`
(`_build_music_stack_sync_cmd` forwarding/sorting;
`full-sync`'s bare-name resolution, unknown-profile failure,
`--fetch-only`, failed-fetch short-circuit, execute/allow-removals
staying plain opt-ins, literal-path passthrough). 296/296 passing across
the whole workspace.

Live-verified end-to-end: `full-sync --profile john --config-root
../../config --playlist "Rise Up" --fetch-only` — bare name resolved,
Apple Music fetch correctly narrowed to just that playlist (3 new
tracks), podcasts synced per existing default behavior (no `--show`
filter given), stopped cleanly before the device stage.

Level 1 (a per-fetcher `sync-profile` subcommand, e.g.
`fetcher-apple sync-profile`) was never built and is now superseded —
`music-stack sync`'s cross-fetcher orchestration already gives the same
result at a higher level, so there's no remaining need for it.

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

## fetcher-ytmusic: downloads unblocked — deno + bgutil-ytdlp-pot-provider fixes the PO Token gate

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

**Fixed (2026-07-20)**: rather than re-shelving indefinitely, set up the
real fix once the tradeoff (real infrastructure, no recurring cost —
unlike the Apple MusicKit path investigated the same day, which needs a
$99/year Developer Program membership) looked worth it:
1. `sudo pacman -S deno` — resolves the JS-runtime/signature-solving
   half on its own (the user ran this; needs a real terminal, `sudo` has
   no passwordless/askpass path in this environment).
2. Cloned `Brainicism/bgutil-ytdlp-pot-provider` (pinned tag `1.3.1`)
   into `services/fetcher-ytmusic/pot-provider/` (gitignored — third-
   party companion service, not vendored into this repo), ran its Deno-
   based HTTP server (`deno run --allow-env --allow-net --allow-ffi=.
   --allow-read=. ../src/main.ts`, listens on `127.0.0.1:4416`) — using
   Deno for the server too (not Docker/Node) avoids a second, redundant
   JS runtime alongside the one yt-dlp itself already needed.
   **Must stay running continuously** — it mints tokens on demand,
   there's no cached/offline mode.
3. `uv add bgutil-ytdlp-pot-provider` (the pip-installable yt-dlp
   plugin half) into the root workspace — yt-dlp auto-detects the
   running server once this is installed, no explicit flag needed for
   the PO Token part.
4. One more piece surfaced only once the above got far enough to reach
   it: yt-dlp also wants a "remote component" challenge-solver script
   downloaded on demand (`--remote-components ejs:github`) — without
   it, signature solving still fails even with deno + the PO Token
   server both working. `fetcher_ytmusic/download.py`'s
   `_run_ytdlp_single_track` now always passes this flag.

**Live-verified end to end** against the real "Semaphore" playlist
(`config/profiles/john.yaml`) with all three pieces running together:
all 31 tracks downloaded successfully (`new tracks: 31, already known:
0`, zero failures) — real, valid `.m4a` audio confirmed via `ffprobe`
(correct duration, `aac` codec). Previously every single track failed
identically with zero usable formats.

**Status**: done, shipped 2026-07-20. Running the companion server is a
real, permanent operational requirement now (not optional polish) —
document this prominently in `services/fetcher-ytmusic/README.md` and
consider it for `docker-compose.yml` (its own service, matching how
`sync-orchestrator` is the one thing that *can't* be containerized
rather than assuming everything must fit one shape) so it starts
automatically instead of being a manually-run background process.

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

## Future: parallel device-side hashing (iOpenPod PR, not yet investigated)

User flagged (2026-08-18) an open iOpenPod PR that runs device-side
hashing with 4 parallel workers for devices without a mechanical hard
drive (i.e. flash/SSD-based iPods, where the hashing bottleneck is CPU/
USB-protocol overhead rather than physical seek time, so parallelizing
actually helps instead of thrashing a spinning disk). Relevant to the
exact cold-cache cost documented above (a first-time hash of ~6,300
files over USB took most of an hour even with the cache-flush workaround
in place, since a from-scratch reformat/full-resync has no warm cache to
hit yet). Not yet located/reviewed — no PR number or link captured yet.

**Status**: not investigated. Next session: find the actual PR on
https://github.com/TheRealSavi/iOpenPod, check whether it's merged into
a version newer than what this project currently pins, and whether this
device's own drive (mechanical vs flash — worth confirming for this
exact iPod Classic 7th Gen/MC293 unit before assuming the speedup
applies) would actually benefit.

## iopenpod (PyPI `iopenpod==1.66.2`): 5th/5.5th-gen "iPod Video" artwork — fixed, two distinct root causes

Real device: `lsusb` "ID 05ac:1209 Apple, Inc. iPod Video", on-device
`SysInfo` `ModelFamily: iPod Video`. Two genuinely separate bugs had to
be found and fixed in sequence before album art displayed — compressed
here from a much longer blow-by-blow (several rounds of "logs/byte-data
look correct" that each still turned out not to be the actual fix; the
real methodology lesson is **log/byte-level evidence is not proof —
only the device's own screen or `.ithmb` file mtimes settle it**, learned
the hard way across ~4 false-positive rounds before landing on each real
fix below).

### Bug 1: device identity collapses to an ambiguous placeholder, silently picks the wrong (or no) format table

Original two findings while getting the M6 headless PoC to write to
this hardware:

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

`device/capabilities.py` (separate from `device/models.py`) does have a
complete, populated entry for `("iPod", "5.5th Gen")` —
`supports_artwork=True`, `cover_art_formats=(1028, 1029)` — confirmed
against real formats already seen unprompted in sync logs
(`F1028_1.ithmb`/`F1029_1.ithmb` already present on-device). So finding
#1 above (missing table data) was wrong; finding #2 (identity
collapsing to an ambiguous placeholder) was the entire real cause: with
`generation=""`, `capabilities_for_family_gen("iPod", "")` can't find
the real table entry and returns `None`.

**Real fix, several rounds to land correctly**: `sync_orchestrator/
sync.py`'s `_capabilities_with_artwork_workaround()` corrects
`info.model_family`/`info.generation` to `"iPod"`/`"5.5th Gen"` directly
on the mutable `DeviceInfo` instance before capabilities resolution runs
(traced the real ArtworkDB writer and confirmed it reads identity
straight off this object, so fixing it once here reaches every
consumer regardless of which module imports what). The *first* shipped
version of this fix was a complete no-op in practice — its `if` check
compared against `model_family == "iPod Video"`, a string `enrich()`
never actually produces (it collapses to the bare placeholder `("iPod",
"")` first) — so every sync kept silently falling through to the
320x320 fallback format despite logs looking clean (format-1060
fallback writes produce the same log shape as a real native-format
write, which is exactly why the no-op went unnoticed at first).
Broadened to `info.model_family == "iPod Video" or (info.model_family
== "iPod" and not info.generation)` and added a test using the *exact*
`("iPod", "")` shape `enrich()` really produces (the original test used
`"iPod Video"` directly and would have passed against the broken code
too). Hardcodes `"5.5th Gen"` specifically rather than auto-detecting
5th vs. 5.5th (they share a USB PID and only differ in
`supports_gapless`/`db_version`; the user confirmed live which
generation the real device is) — would need to become configurable if
this project is ever used against a plain 5th-gen unit.

Confirmed via `lsusb -v`/the device's own `SysInfo`/iOpenPod's own
`iOpenPodSysInfoAuthority` tracking file that there is genuinely no
on-device signal anywhere that says "5.5th Gen" specifically — Apple
reused the same USB PID across 5th/5.5th gen, so this is a real
protocol-level ambiguity, not a bug; the hardcoded generation is this
project's own external knowledge, unreadable from the device itself.
**Fix idea (upstream, unchanged)**: `_restore_usb_pid_identity_if_needed()`
still prefers a coarser cached identity over a more specific one
whenever they textually differ — worth filing against
https://github.com/TheRealSavi/iOpenPod regardless of this local fix.

Even after the identity fix landed correctly (confirmed via
`.ithmb` file mtimes updating and a specific track's artwork
byte-decoded and rendered from the real on-device `ArtworkDB`, proving
correct pixel data + correct iTunesDB↔ArtworkDB linkage end to end),
**album art still did not display on the device's own screen.** A
decisive bisection (checking a track whose artwork was untouched since
the original real-iTunes sync, not written by this project's tooling at
all) also showed no artwork — ruling out "something specific to our new
writes" and confirming this was a regression somewhere in iOpenPod's
writer generally, not a device limitation (this same device's artwork
did display correctly under real iTunes before iOpenPod ever touched
it). iOpenPod's own native GUI independently fails to identify this
device's model too (same underlying `enrich()` bug), but unlike this
project's headless wrapper it has no override and refuses to mount at
all — meaning this project's patched headless path has been the only
thing that's ever gotten this device to sync via iOpenPod at all, with
no independent GUI sync to compare against.

### Bug 2: missing structural chunk in every ArtworkDB `mhii` entry (root cause of the actual "no art" symptom)

Root-caused 2026-07-22 via a real iTunes resync of the same physical
device (a fresh known-good reference) byte-diffed against this
project's own recovered pre-resync backup snapshot. Full write-up,
exact byte-diff evidence, and the fix already lives in
[`docs/iopenpod-artworkdb-missing-mhii-chunk.md`](docs/iopenpod-artworkdb-missing-mhii-chunk.md)
— not duplicated here. One-line summary: every `mhii` entry real iTunes
writes has a third, always-zero 120-byte structural child chunk that
iOpenPod's `_write_mhii()` never emits; firmware silently declines to
render an incomplete-shaped entry rather than erroring. Fixed via
`_apply_missing_artwork_index_chunk_workaround()` in
`sync_orchestrator/sync.py`. Filed upstream against
`TheRealSavi/iOpenPod`, not yet resolved there.

**Live-verified end to end (2026-07-22)**: a real full resync (4470
added, 95 removed, 5534 tracks total) confirmed every `ArtworkDB` entry
now has the previously-missing chunk, matching real iTunes' shape
exactly — **and the user physically confirmed album art displays
correctly on the device's own screen**, for the first time in this
project's history. Status: **FIXED** for the Apple Music /
pre-existing-library case on this device.

**New, separate finding, since fixed**: the `Semaphore` playlist
(YouTube Music, `fetcher-ytmusic`) showed no album art at all. Confirmed
via `mutagen` — these `.m4a` files had no embedded `covr` atom whatsoever
(checked `nimino - Nothing Perfect` and `Franz Ferdinand - Curious`),
whereas an Apple-Music-sourced file (`RAT BOY - BROKEN`) has a real
661KB embedded cover. Root cause: `fetcher-ytmusic`'s `yt-dlp` invocation
(`download.py`'s `_run_ytdlp_single_track`) had no `--embed-thumbnail`
flag, and nothing in its own `tag.py` added artwork afterward — unlike
`gamdl`, which embeds real Apple Music artwork automatically as part of
a normal download. Not the same bug as the one fixed above (that was
about entries that *had* artwork not displaying; this was tracks that
never had artwork data to begin with).

Fixed 2026-07-22 (commit `df9b147`): `TrackMeta` now carries a
`thumbnail_url` populated from `ytmusicapi`'s thumbnails, upscaled from
YouTube's default 60x60/120x120 to 1200x1200 via the same Google
image-proxy URL scheme (confirmed live — accepts arbitrary sizes, not
just a re-scaled blur); downloaded and embedded via a new `set_artwork()`
in `tag.py`. While investigating this, also found and fixed a real
playback bug in the same tracks: they reported correct full duration but
only played ~15-30s on the real device before skipping — root cause was
YouTube serving audio at 48kHz (its platform-native rate) vs. the
44.1kHz click-wheel iPod hardware AAC decoders were designed/tested
against; fixed by forcing yt-dlp's own ffmpeg extraction pass to
resample to 44100Hz. Forced a full redownload of all 31 Semaphore
tracks (old 48kHz/no-artwork files deleted from state db + disk first)
and synced to the real device (2m20s — the `FingerprintCache` workaround
below was warm since nothing had wholesale-rewritten the device since
the prior sync, confirming that optimization holds up under normal
repeat use, not just after a big rewrite).

**Status: FIXED, live-confirmed** — the user directly confirmed album
art visible on the device's own screen after this sync, covering both
the pre-existing/Apple-Music case (the `mhii`-chunk fix above) and now
YouTube Music tracks too.

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

## Bug: already-listened episodes downloaded anyway — fixed via M8's local play-state signal

Observed live 2026-07-19: after the combined sync, some episodes that had
already been listened to were still downloaded. `sync_podcast()`'s
`sync_unplayed_only` filter relied solely on `list_episode_states()`
having a row for that episode — and we already know from M5
(`podcast_manager/api.py`'s `EpisodeState` docstring) that Pocket Casts
"only returns a row here for episodes the user has actually interacted
with... there is no row at all for an episode still in its default/
untouched (unplayed) state." A real listen not reliably producing that
row (sync lag between devices, listened to via a different app, etc.)
meant a genuinely-played episode could be incorrectly treated as unplayed
and downloaded again.

**Fix**: once M8's device read-back existed (`sync_orchestrator/
playstate.py` recording real on-device listening progress into
`state.sqlite`, independent of whether Pocket Casts' own API ever saw
it), `sync_podcast()` (`podcast_manager/download.py`) was updated to
treat an episode as played if *either* Pocket Casts' `EpisodeState` OR
the local `state.sqlite` row already says so — closing exactly the gap
above, using a signal we now have. Also fixed a related bug the same
change surfaced: the old code unconditionally overwrote
`played`/`played_up_to` from Pocket Casts on every `record_episode()`
call, which would have silently *undone* an M8 device read-back's
`played=True` the moment Pocket Casts hadn't (yet, or ever) caught up —
now merged (OR / max) instead of overwritten, mirroring how
`record_episode`'s own `ON CONFLICT` already leaves `pending_push`
untouched. Two regression tests added
(`test_sync_podcast_excludes_episode_played_locally_but_not_on_pocket_casts`,
`test_sync_podcast_does_not_downgrade_locally_played_episode`).

Residual, unfixable gap: an episode listened to on a device/app that
never syncs to Pocket Casts *and* never gets read back from our own iPod
sync either (e.g. deleted from the device before a sync runs) still has
no signal available to us at all — genuinely outside what either source
can catch.

**Status**: done, shipped 2026-07-20.

## Future: decide what happens to a track's local file when it's removed from an "absolute" playlist

Noted 2026-07-21. `sync_mode: absolute` (the default) means the local
`.m3u8` mirrors the source playlist's current contents exactly,
including removals — but "removed from the playlist" today only ever
means "no longer listed in that one `.m3u8` file." The actual downloaded
track under `library/music/` is never touched, deleted, or reconsidered
by anything in the fetch pipeline just because a playlist stopped
referencing it. Whether that's actually right depends on a real
decision this project hasn't made yet:

- If the track is only ever referenced by that one playlist, keeping
  the file around forever is arguably silent bloat — nothing will ever
  clean it up.
- If the track is shared across other playlists, or is something the
  user actually wants in their library independent of any one playlist
  (most likely case for most tracks), deleting it just because one
  playlist rotated it out would be actively wrong.
- iOpenPod's own device-side dedup/removal logic operates on `pc_folders`
  contents, not on "was this in a playlist" — so this decision is really
  about `library/music/` itself (and by extension `library-manager`),
  not about anything sync-orchestrator or the fetchers currently do.

**Fix idea**: needs an explicit policy decision before any code change —
e.g. "never auto-delete, this is a library not a cache" (simplest, safe
default) vs. "delete a track's file only when zero playlists/other
references point to it anymore" (real reference-counting, meaningfully
more complex, touches `library-manager`'s dedup/state tracking). No
implementation started either way.

**Status**: not started — decision needed, not yet made.

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

Hit four times now (`sync-orchestrator`, `fetcher-spotify`, then
`sync-orchestrator` again for the nested `external_library.selections`
mapping validator, then `sync-orchestrator` a third time for M8's
`StateDB.list_episodes()` — `AttributeError: 'StateDB' object has no
attribute 'list_episodes'` on a real sync run, right after the exact
same session that had just added and tested it against the root
workspace): a standalone
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

## M9 (Automation) — shipped: scheduled fetch, multi-profile matching, udev auto-sync

All three of M9's acceptance criteria implemented this session (see
`music-stack-planning.md` §3/§9 for the design, added the same session
before implementation):

- **Scheduled fetch, independent of device presence.** `PlaylistEntry.
  fetch_schedule`, `ProfilePodcastsConfig.fetch_schedule`/per-show
  `ShowOverride.fetch_schedule`, and `ProfileConfig.fetch.schedule` (all
  cron expressions, validated via `croniter` at config-load time —
  `common/models.py`'s `CronSchedule` type). Resolution precedence:
  per-playlist/per-show > podcasts-level > profile default
  (`common/schedule.py`'s `iter_fetch_targets`/`resolve_fetch_scope`).
  A new `fetch_runs` table in `state.sqlite` (`common/state.py`) tracks
  `last_fetched_at` per playlist/show, keyed by `(target_type,
  target_id)` — no `profile` column, since the db is already one-file-
  per-profile. New `services/fetch-scheduler` service (containerizable —
  no USB access needed) runs the actual tick loop, reusing `music_stack_
  cli.orchestrate.run_sync` rather than reimplementing fetch
  orchestration; supports both a long-running `docker-compose.yml`
  service (`restart: unless-stopped`, the first in that file) and a
  `--once` single-tick mode for cron/systemd-timer deployment instead.
- **Multi-profile device matching.** `sync_orchestrator/device.py`'s
  `find_matching_profile(profiles)` tries each profile's existing
  `find_matching_device` in turn; raises `AmbiguousDeviceMatchError` (not
  a silent pick) if a connected device matches more than one profile.
- **udev-triggered sync.** New `sync-orchestrator auto-sync` subcommand:
  polls for a matching profile (`--wait-seconds`, since udev's ADD event
  fires before the filesystem is actually mounted), then syncs with
  `--execute --allow-removals` **always on, no opt-out** — per explicit
  instruction this session ("plug in your ipod before bed, wake up to
  everything synced up as configured"), not a more cautious partial sync.
  udev rule + trigger script in `services/sync-orchestrator/udev/`,
  installation documented as a manual `sudo` step in that service's
  README (never auto-installed).
- **Opportunistic pre-fetch, added mid-session per a follow-up
  instruction**: `auto-sync` normally never fetches (keeps device-plug-in
  fast), but if any of the matched profile's targets have their next
  scheduled fetch due within `--pre-fetch-horizon-hours` (default 4) of
  the connection, it pre-fetches just those targets first — invoked as a
  **subprocess** call to `music-stack sync` (not an in-process
  `run_sync` import), deliberately: `sync-orchestrator` stays standalone
  specifically to keep its `iopenpod`/PyQt6 dependency tree from merging
  with `music-stack-cli`'s (gamdl, yt-dlp, etc.), same reasoning as
  `fetcher-spotify`'s isolation. A failed pre-fetch only warns and falls
  through to syncing whatever's already local.
- **Found and fixed via live testing** (`fetch-scheduler --once` against
  the real `alice.yaml` example profile): a target whose source kept
  failing (bad Spotify support, fake Pocket Casts credentials) printed
  nothing at all — `TickResult` only surfaced unexpected exceptions, not
  `run_sync`'s own per-source `source_errors`. Fixed by adding `TickResult.
  source_errors` and printing it, so a persistently-broken auth doesn't
  go silently invisible forever (same "loud not silent" principle as the
  gamdl cookie-expiry risk noted elsewhere in this file).

**Status**: implemented and unit-tested (schema/state/schedule-resolution/
scheduler-loop/device-matching/auto-sync all covered); live-verified
`fetch-scheduler --once`/`--dry-run` against real config and `sync-
orchestrator auto-sync` against a real (disconnected) device end-to-end.
**Not yet live-verified**: the pre-fetch subprocess path and the full
udev rule installation against a real connected device — both need an
actual iPod plugged in to confirm end-to-end, left for the user to try
next.

**Follow-up found during real udev install/testing**: first live attempt
to trigger the rule found the auto-mount assumption didn't hold — the
device showed as connected (`lsusb`: `05ac:1209`) and the rule correctly
matched (confirmed via `udevadm test`, which shows queued `RUN{program}`
commands without executing them), but nothing had mounted its filesystem
on the host (`/proc/mounts` had no vfat/hfsplus entry for it — only
`/boot/efi`), so `auto-sync` had nothing to find regardless of whether
the script itself ran correctly. (Separately also found and fixed two
real install mistakes along the way: `REPO_ROOT` in the installed
`/usr/local/bin/music-stack-auto-sync.sh` pointed at `/home/john/
music-stack`, missing the `/Music` path segment, which made the log
redirect target's parent directory not exist — a background/backgrounded
`&` command failing this way is invisible, since `set -e` in the parent
script doesn't propagate a backgrounded job's failure; and a fix applied
to the repo's copy of the script wasn't re-copied to the installed
`/usr/local/bin` location, which is the one udev actually executes.)

**Fix**: `auto-sync` no longer assumes something else has mounted the
device. `device.py` gained `mount_candidate_devices()` — best-effort
`udisksctl mount` of every currently-unmounted vfat/hfsplus partition
(discovered via `lsblk`, which sees unmounted partitions too, unlike
`/proc/mounts`) — called every poll tick before `find_matching_profile`.
Per-device mount failures are swallowed (an unrelated stuck USB drive
must never block finding the real iPod). `find_matching_device` itself
is unchanged — still assumes already-mounted, since that's still correct
for the interactive `sync` command. 6 new tests
(`test_device.py`/`test_cli.py`).

**Second follow-up, same install session**: with auto-mount in place,
`sudo udevadm trigger` was re-run — the device *did* get auto-mounted
(confirmed: `/dev/sdb2` mounted under `/run/media/root/JOHN_S IPOD`, i.e.
`mount_candidate_devices()` genuinely worked, called as root via udev's
own execution context) — but `auto-sync.log` stayed a 0-byte file
(`mtime == birth time`, zero bytes ever written) and no
`sync-orchestrator`/python process was left running at all. Root cause:
the `RUN+=` script (`music-stack-auto-sync.sh`, `setsid ... &`-detached)
runs inside `systemd-udevd`'s own per-device **cgroup** — `setsid`
detaches the process's *session*, but not systemd-udevd's cgroup
tracking, and systemd kills that whole cgroup once it considers the
device's event handling finished. The mount syscall (fast, happens
early) completed before the kill; the real sync (which the project's own
prior notes already establish can take 20-50+ minutes) never got the
chance, and any buffered stdout never flushed.

**Fix**: replaced the `RUN+=` shell-script approach entirely with
systemd's documented pattern for exactly this situation — the udev rule
now does `TAG+="systemd", ENV{SYSTEMD_WANTS}="music-stack-auto-sync.
service"` instead of `RUN+=`, handing the device off to a real,
independent systemd unit (`udev/music-stack-auto-sync.service`, `Type=
oneshot`, `PYTHONUNBUFFERED=1`, `StandardOutput=append:.../auto-sync.
log`) that's fully outside udev's own process/cgroup lifecycle. The old
`music-stack-auto-sync.sh` wrapper script is deleted — no longer needed,
`ExecStart=` calls the venv binary directly. Also worth noting for later:
VirtualBox is separately queued to claim this same USB device per
`udevadm test`'s own output on this machine — if a running VM has a USB
filter for it, that could still intercept the device before either
udev/systemd or the host ever gets to it; not yet an issue in this
session's testing, but worth checking first if a future attempt sees the
device disappear from the host entirely.

**Status**: implemented, not yet live-confirmed end-to-end (needs a real
install + re-trigger with the new `.service` file — left for the user to
try next).

## `eject_device()` was too aggressive — stopped the iPod charging (fixed)

User reported live: after a real `sync`/`auto-sync` run ejects the
device, it doesn't charge — but ejecting the same device through a
desktop file manager does. `eject_device()` (`device.py`) did two
`udisksctl` calls: `unmount`, then `power-off` — the second one added
deliberately (see its old docstring) on the theory that a plain unmount
left the iPod stuck showing "connected to computer." That belief wasn't
re-examined against the actual side effect: `udisksctl power-off`
deauthorizes/powers down the USB port electrically (the same mechanism
meant for "safe to physically unplug an external HDD now"), which cuts
USB power delivery entirely — that's what was stopping it charging. A
file manager's own eject only unmounts; it doesn't power off the port,
which is exactly why it kept charging.

**Fix**: `eject_device()` now only unmounts, matching file-manager
behavior. Dropped `power-off` entirely, along with the now-unused
`_PARENT_DRIVE_RE` regex/`re` import that existed only to compute the
parent drive for that call. Test `test_eject_device_unmounts_then_
powers_off_parent_drive` replaced with `test_eject_device_only_
unmounts_does_not_power_off`; `test_eject_device_raises_on_power_off_
failure` removed (no longer applicable).

**Status**: fixed, matches the file-manager behavior the user compared
it against. Not yet re-confirmed live that the device actually charges
after this — should be checked on the next real eject.

**Correction, same day**: the unmount-only fix above was itself wrong —
user reported live it was still "too soft": the device stayed visible in
the file explorer and the iPod still showed "do not disconnect" after
our eject ran. Rather than guess a third time, eavesdropped the real
D-Bus traffic (`busctl monitor org.freedesktop.UDisks2`) while
triggering an actual GUI eject in the file manager, to see exactly what
it calls. Finding: a single `Drive.Eject()` call (`org.freedesktop.
UDisks2.Drive.Eject`, empty options, going through a polkit
`org.freedesktop.udisks2.eject-media` check as the logged-in user) — not
`Filesystem.Unmount`, not `Drive.PowerOff`. No separate `Unmount` call
appears anywhere in the ~97k-line capture — `Eject()` handles unmounting
internally. The resulting `PropertiesChanged` signal on the Drive object
sets `MediaAvailable=False`/`Size=0` — a SCSI/media-layer "media is
gone" signal (this is what gets the iPod out of "connected" mode) —
distinct from and unrelated to USB port power state, which is exactly
why it doesn't stop charging: `Eject()` and `PowerOff()` operate at two
different layers (media-removal vs. USB-port-power), and the earlier fix
conflated "not doing PowerOff" with "doing the right thing" without
confirming what `Eject()` specifically does.

**Fix**: `eject_device()` now calls `udisksctl eject -b <drive>`
(`Drive.Eject()`) instead of `udisksctl unmount`. Re-added
`_PARENT_DRIVE_RE`/`re` import (removed in the previous fix, needed
again since `eject` operates on the whole drive, not a partition, same
as `power-off` did). Tests updated to match
(`test_eject_device_calls_udisksctl_eject_on_parent_drive`,
`test_eject_device_raises_on_eject_failure`).

**Status**: fixed, this time grounded in an actual observed trace of
the real behavior being replicated rather than a plausible-sounding
theory. Not yet re-confirmed live — should be checked on the next real
eject that it both leaves "connected" mode AND keeps charging.

**Third correction, same day**: the `udisksctl eject` fix above failed
the very next real auto-sync run — confirmed live in `auto-sync.log`:
`udisksctl eject failed: Unknown command 'eject'`, followed by
udisksctl's own usage text listing its actual verbs (`mount`,
`unmount`, `power-off`, `info`, `dump`, `status`, `monitor`, `unlock`,
`lock`, `loop-setup`, `loop-delete`, `smart-simulate`) — no `eject`
anywhere. The `Drive.Eject()` D-Bus method a GUI eject calls (confirmed
in the previous fix) genuinely exists, but this system's installed
`udisksctl` CLI simply never exposes it as a subcommand — a CLI/D-Bus
API coverage gap, not a wrong understanding of what needs to happen.

**Fix**: switched to the classic standalone `eject` utility (util-linux,
confirmed present: `eject from util-linux 2.42.2`) instead of
`udisksctl eject` — same "safely detach a removable drive" intent,
long-predates udisks2, doesn't depend on udisksctl's CLI covering every
D-Bus verb. By the time this was being tested live, the device had
already disconnected (unplugged mid-investigation), so — unlike the
`Drive.Eject()` finding above — this one is **not yet confirmed** to
produce the identical `MediaAvailable=False` signal; it's a reasonable
substitute (same category of operation) rather than a verified-identical
one. `_PARENT_DRIVE_RE` kept (still needed — `eject` also operates on
the whole drive, not a partition). Tests updated
(`test_eject_device_calls_eject_on_parent_drive`).

**Status**: FIXED, live-confirmed. User confirmed the `eject` (util-linux)
version works correctly on a real connect — third attempt at this
specific fix in one day (unmount-only → udisksctl eject → plain eject),
this one closes it out.

## Automatic library dedup/cleanup + backup retention — shipped

Two long-standing gaps closed while thinking about long-term homelab
deployment: `library-manager`'s dedup/cleanup was 100% manual (nothing
ever called it automatically), and `state/device_backups/` had no
retention policy at all — confirmed live it had already grown to 199GB
(disk 77% full) with sync now fully automated (udev + fetch-scheduler)
and nothing capping it.

**Design**: both wired into `fetch-scheduler`'s existing tick as global
(not per-profile) maintenance, gated by simple `bool` enable flags
(`library_manager.dedup_enabled`/`cleanup_enabled`,
`backups.prune_enabled` in `global.yaml`) rather than their own cron
schedules — per explicit instruction, they run as a post-step whenever
*any* profile actually fetches this tick, not on independent timing.
New `common/backups.py` (zero `iopenpod` dependency — reads the plain
JSON snapshot manifest + content-addressed blob format directly) does
retention: a snapshot is kept if its rank among that device's snapshots
is `< keep_last` **OR** its age is `<= max_age_days` — pruned only if
both fail, deliberately conservative. Blob garbage collection is a
strict two-phase operation: decide+delete every device's snapshot
manifests first, then only once every device's keep-set is final,
compute the union of hashes referenced by every *surviving* snapshot
across *every* device directory (confirmed live: `blobs/` is a single
store shared across different device_ids, not per-device) and delete
anything not in that union.

**Real findings from live testing against the real 199GB store**:
1. Real snapshot JSON timestamps have no timezone info (`iopenpod`'s
   `BackupManager` writes naive ISO timestamps) — comparing against an
   aware `now` raised `TypeError` on first live dry-run. Fixed by
   treating a naive timestamp as UTC (matches every other timestamp
   convention already used in this project).
2. John's real backup data has the *same physical iPod* under two
   different `device_id` directories (`000A270015AE6188`,
   `8K6382K4V9S`) — a different serial/FireWire-GUID read across
   sessions. `resolve_retention_map`'s volume-label matching handles
   this by matching *every* device dir whose sampled `device_name`
   equals a profile's `match_value`, not just the first.
3. User-confirmed retention defaults given real disk pressure: keep
   last 3 snapshots OR 14 days (matches `library-manager`'s own
   quarantine grace period) — dry-run against real data confirmed this
   deletes nothing today (all existing snapshots are recent), only
   starts pruning once a device accumulates more history.

**Live-confirmed end-to-end** (real, non-dry-run `fetch-scheduler
--once` run): dedup scanned 1,158 tracks and quarantined 1 real
duplicate (confirmed on disk under `library/music/.duplicates/`);
cleanup found nothing old enough yet; backup-prune deleted 0 snapshots
and 1 orphaned blob (confirmed: blob count on disk dropped from 10,972
to 10,971) — every number matched what the preceding dry-run predicted
exactly.

**Status**: shipped and live-confirmed. `profile: global` is now a
reserved profile name (`common/config.py`) to keep it free for any
future non-profile-scoped state.

## Distribution: why sync-orchestrator isn't containerized too

Considered whether the whole stack — including `sync-orchestrator` +
its udev rule + systemd service — could collapse into a single Docker
container, for easier distribution.

`systemd-udevd` itself can't reasonably run inside a container (needs
`--privileged` + the host's `/sys`/`/dev`/cgroup hierarchy shared in,
and conflicts with the udev daemon already running on the host for the
same devices) — but that's not actually required: `auto-sync`'s own
detection (`mount_candidate_devices()`/`find_matching_profile()`) never
depended on udev *events*, only used udev as an efficient trigger
instead of polling continuously. A privileged container running an
infinite poll loop instead would work detection-wise (this is the
established pattern for "container needs to react to USB hotplug" —
e.g. how Home Assistant's USB integrations and Zigbee2MQTT handle it).

The real blocker is mounting: actually mounting/unmounting a real
filesystem from inside a container needs `--privileged` (or at minimum
`CAP_SYS_ADMIN` for the `mount()` syscall) plus the host's `/dev`
shared in for the block device nodes — at that point the container
boundary isn't providing meaningful isolation anymore for this
component specifically, just extra namespace/cgroup plumbing on top of
what's effectively bare-metal access anyway.

**Decision**: keep the two-tier split — Docker Compose for the
genuinely containerizable services, a separate small bare-metal
install (systemd unit + udev rule) for the USB-touching piece. For
*distribution*, the win isn't forcing that piece into Docker, it's
packaging its install (currently manual README steps) into a proper
installer — deferred until the web GUI (Phase 4) exists, since
service-selection + install is natural GUI-setup-flow work.

## M15 audiobook spike, round 2 — odmpy confirmed dead against the real account, manual extraction path found instead

Revisited the M15 spike (see `music-stack-planning.md` §7a) and
confirmed live against a real account (not just inferred from old
GitHub issues) that `odmpy` is definitively dead: OverDrive killed
`.odm` manifests entirely on 2025-01-31; the `libby`-setup-code auth
path (the one alternative that looked promising) is rejected because
Libby's current app no longer exposes a generic "new device"
code-generation flow at all (confirmed by trying the one code type
that does still generate, Sonos speaker linking — genuinely rejected by
odmpy's request, not just expired). The other candidate
(`bookbonobo/libby-download-extension`) is also stale. Full detail in
git history if ever needed again; the conclusion is what matters going
forward and is already captured in `music-stack-planning.md` §7a and
`services/audiobook-manager/README.md`.

**Manual extraction method found, real and current**: Libby's *web*
player (libbyapp.com, desktop Chromium only) streams each audiobook as
plain per-chapter MP3 files over HTTP, visible and downloadable via
browser DevTools' Network tab — no root, no DRM defeat, no paid tool.
This is the acquisition path `services/audiobook-manager/` (below)
picks up from.

**2026-07-27 — merge/tag pipeline shipped as `services/audiobook-manager/`.**
Built the rest of the manual drop-in workflow from the finding above:
`ffmpeg` concat-demuxer + FFMETADATA chapters to merge raw MP3 parts
into one AAC `.m4b`, then `beets` + the real `Neurrone/beets-audible`
plugin (PyPI `beets-audible`, **not** the stale 2022 `seanap` fork) for
Audible/Audnex metadata lookup and tagging. Shipped as its own
standalone workspace member rather than a `library-manager` subcommand
— `beets` 2.12 pulls in `numpy`/`scipy`/`numba`/`llvmlite` as
unconditional base dependencies (confirmed via `uv sync`, real weight,
no version conflicts against this workspace's existing lock) plus live
network calls, which would have made `library-manager` (deliberately
kept minimal/offline) much heavier for every user, not just audiobook
users. Docker Compose entry gated behind its own `audiobooks` profile
for the same reason.

Two real things worth remembering for next time this area is touched:
- `beet import -q`'s success/failure isn't observable by parsing stdout
  (not a stable contract) — detected instead by diffing
  `beets.library.Library`'s item set before/after the subprocess call,
  which needs zero network to query directly since beets' db layer is
  plain sqlite.
- Live-verified this session that mutagen tag writes and beets' own
  `scrub` plugin's `MP4.delete()` both preserve the MP4 `chpl` chapter
  atom — safe to merge-then-tag in that exact order with no risk of the
  tagging step silently stripping chapters back out.

`sync-orchestrator`'s existing `--pc-folder` flag already threads
`library/audiobooks` into a real sync plan with zero new orchestrator
code (confirmed live) — passed manually per sync for now, not wired in
as a persistent default yet.

**Live end-to-end run against the real Franz Kafka - The Trial data**
(12 real MP3 parts fetched earlier from the home server): merge step
produced a real 263MB/8.83-hour AAC `.m4b` with 12 correctly-bounded
chapters. `beet import -q` (the actual mode `import-audiobook`/`tag`
use) correctly **skipped** it — Audible has 10 different real editions
of this public-domain classic (Stream Readers, Recorded Books, Naxos,
Tantor, Penguin Classics, etc.), all scoring the same 25%/0.75-distance
match against a bare folder-name query, too ambiguous for quiet mode to
pick automatically. This is expected, correct behavior, not a bug — the
"Known gap" fallback documented in `services/audiobook-manager/README.md`
exists precisely for this case. Ran the same import interactively
instead (`beet import`, no `-q`) and manually picked candidate #1
(Stream Readers edition, narrated by Daniel Brooks) — it completed for
real: file moved to `library/audiobooks/Franz Kafka/The Trial/01 -
merged.m4b` alongside real `cover.jpg`/`desc.txt`/`reader.txt` sidecars,
`stik` confirmed `[2]`, all 12 chapters and the full 8.83-hour duration
intact post-move, real Audible-sourced tags (author, narrator, genre,
description) all present. Fully confirms the merge-then-tag pipeline
and the skip/retry design both work correctly against real data; only
the final on-device step (`--pc-folder library/audiobooks --execute`
against the actual iPod) is still pending, deferred until the device is
next connected.

**2026-07-27 (later same day) — audiobooks config + automatic sync
inclusion.** Resolved the "manual `--pc-folder` flag, not a persistent
default" follow-up flagged above: added `AudiobooksConfig` to
`common/models.py` (`mode: include/exclude` + `selections`, same
path-fragment-prefix shape as the existing `ExternalLibraryConfig`,
factored the nested-mapping-flattening validator logic out into a
shared `_flatten_nested_selection_entries` helper both classes use).
Deliberately different default from `ExternalLibraryConfig`: empty
`selections` + `include` mode means **sync every audiobook** (not "sync
nothing," which is what `ExternalLibraryConfig` does in that case) —
most profiles won't want to curate audiobooks at all, so the no-config
and default-config cases both behave the same way.

`sync_orchestrator/selection.py` gained `resolve_audiobooks_folder()`,
reusing the existing `resolve_selected_files`/`build_staging_dir`
functions (same symlink-staging-dir technique `external_library` uses,
for the same reason — avoids `iopenpod`'s removal-detection treating a
narrowed scan as "removed from PC"). `sync.py`'s `plan_sync()` now
includes `library_root/audiobooks` in `pc_folders` automatically
whenever the directory exists, skipped silently if it doesn't (a
profile with no audiobooks imported yet shouldn't need any special
config or throw an error). `--pc-folder library/audiobooks` still works
as a manual override/escape hatch but is no longer necessary.

**2026-07-27 (later same day) — three real bugs found from live use, all
fixed:**

1. **Audiobook track title showed as "merged" on-device.** Root cause:
   `merge.py`'s output was never given a title tag, and its staging
   filename literally is `merged.m4b` — beets-audible's own track-title
   assignment copies the source file's existing tag/filename-derived
   title through unchanged whenever the import isn't a confident,
   one-file-per-Audible-chapter match (which a single merged file with
   embedded chapters never is), rather than overriding it from the
   matched book's real title. Fixed by seeding a real title via a global
   `title=` line in the FFMETADATA file `merge_parts_to_m4b` already
   builds for chapters (`derive_title_from_folder_name`: "Author -
   Title" -> "Title", falling back to the whole folder name). Manually
   retagged the already-synced Kafka file directly (`©nam` "merged" ->
   "The Trial") rather than requiring a full re-import.
2. **A finished podcast episode wasn't removed from the device.** User
   pressed skip about a minute before the end of an hour-long episode.
   `playstate.py`'s `resolve_played_states` previously required
   `recent_playcount > 0` before even checking position — but a
   click-wheel iPod's own play-count most likely only increments on a
   *natural* completion, not skip/next, so a near-complete-but-skipped
   episode never got marked played regardless of how far in the
   bookmark position was. Fixed: when a known duration exists, position
   alone (>= `PLAYED_THRESHOLD`) now decides played state, independent
   of `recent_playcount` — `recent_playcount` is only consulted as a
   fallback when duration is unknown. Not independently verified against
   raw device Play Counts data (device wasn't connected at diagnosis
   time) — worth confirming against real data next real sync.
3. **The Kafka audiobook's cover art showed up in the iPod's Photos
   app, not just as track artwork.** Root cause: `sync.py`'s `pc_folders`
   were bare strings, and iopenpod's own folder-scan defaults an
   unqualified folder to *every* media type (music/video/photo/
   playlists — `infrastructure/media_folders.py`'s `DEFAULT_MEDIA_TYPES`),
   not just music. beets-audible's own `write_description_file`/
   `fetch_art` config (this project's default) writes a real loose
   `cover.jpg` alongside every imported book — which the same PLAN
   operation's photo scan (`sync/photos.py`'s `scan_pc_photos`, driven by
   `MEDIA_TYPE_PHOTO`) picked up and synced as an actual device photo,
   completely independent of the (correct, unaffected) per-track
   artwork-embedding path (`art_extractor.extract_art_with_folder`).
   Fixed by wrapping every folder in `MediaFolderEntry(...,
   media_types=(MEDIA_TYPE_MUSIC,))` before handing them to
   `EngineRequest` — this project has no photo-sync feature at all, so
   every folder we build should always have been music-only. The
   already-synced stray photo isn't cleaned up by this code change
   alone; expect it to show up as a proposed removal on the next real
   `--execute` sync (PC-side photo scan will now find zero photos across
   every folder) — gated behind the same `--allow-removals` flag as any
   other removal, or delete it manually on-device if it doesn't.

**2026-07-27 (same day, real incident) — the photo fix above deleted
every playlist from a real device.** `music-stack-auto-sync.service`
fired twice over lunch (13:39 and 14:09, `state/auto-sync.log`). Run 1
synced the Kafka audiobook + its stray photo for real (before the fix
above landed). Run 2's plan showed `playlists_to_remove=11` and the
execute log confirmed all 11 were actually removed
(`[assemble_commit] 11/11 — Removed playlist: ...`) — plus the
`WARNING:iopenpod.sync.audio_fingerprint:Unsupported format for
fingerprint storage: .m4b` the user separately flagged (benign: only
means .m4b files skip the cached-fingerprint-tag optimization, falls
back to normal path/tag identify — confirmed the same log shows the
audiobook track correctly identified regardless, not a contributing
cause here).

Root cause of the playlist wipe: the photo fix above restricted
**every** pc_folder — including `library_root/playlists/{profile}` —
to `media_types=(MEDIA_TYPE_MUSIC,)`, dropping `MEDIA_TYPE_PLAYLISTS`.
iopenpod's PC-side scan went blind to every `.m3u8` in that folder, and
its removal planning treats anything previously synced but no longer
"seen" as removed from the PC — the exact `allowed_paths` mechanism
`selection.py`'s own module docstring already warned about, just
triggered a different way. `auto-sync` always runs with
`--allow-removals` (`cli.py`'s `_cmd_auto_sync`), so this went through
with zero human review.

Real damage was limited: `to_add=0 to_remove=0` for tracks (only
playlists were affected), and every `.m3u8` file was confirmed still
intact under `library/playlists/john/` — so no source data was lost,
just the on-device playlist objects. Fixed by extracting a
`build_media_folders()` helper (`selection.py`) that scopes to
`(MEDIA_TYPE_MUSIC, MEDIA_TYPE_PLAYLISTS)`, with a regression test
asserting both types survive. The next real sync should re-add all 11
playlists automatically (their source `.m3u8` files never changed) —
no snapshot restore needed, just re-run once this fix is in place.
**Lesson**: any future narrowing of `pc_folders`/media types needs to
be checked against every folder actually being passed in, not just the
one that motivated the change.

**2026-07-27 (same day) — playlist recovery confirmed, but the log
looked alarming for an unrelated reason.** The next auto-sync (14:29)
correctly re-added all 11 playlists with zero track changes
(`to_add=0 to_remove=0`, `playlists_to_add=11`), exactly as predicted —
the fix above worked. But `cli.py`'s `_print_plan` had its own real,
separate bug: `p.get('title') or p.get('name') or p` was checking the
wrong case — real iopenpod playlist dicts key the name as `'Title'`
(capitalized), so both `.get()` calls missed and it fell through to
printing the **entire raw dict** per playlist, including every track's
`source_path`/`db_track_id` in its `items` list. For an 80-track
playlist that's a genuine wall of unreadable text in the log — enough
that a totally clean, successful sync looked like something had gone
wrong. Fixed to check `'Title'` first, with the old checks kept as
fallbacks; regression test added asserting a big `items` list never
ends up in the printed output.

## iopenpod 1.66.2 → 1.67.0 — the 5.5th-gen identity workaround is now dead code; the ArtworkDB mhii chunk workaround is not

**2026-07-30.** iopenpod shipped 1.67.0 with, per its own release notes,
"Fixes to device identification flow on Linux." Bumped
`services/sync-orchestrator/pyproject.toml`'s pin, `uv lock` + `uv
sync`, full suite green (77 tests) before touching anything else. The
internals our two monkeypatches touch (`get_current_device_for_path`,
`capabilities_for_family_gen`, `artworkdb_writer.artworkdb_chunks
._write_mhii`) all still exist with identical signatures, so nothing
broke outright — but worth checking whether either patch had become
unnecessary, or worse, is now fighting a fixed upstream instead of a
broken one.

**What actually changed**: a new `device/linux_identity.py` module plus
a provenance-ranked field-source system in `enrich()`
(`_set_field_from_source`, `_source_rank`/`device/authority.py`).
Crucially this requires iopenpod's own separate udev rule
(`61-iopenpod.rules`, asset bundled in the package, distinct from this
project's own `99-ipod-music-stack.rules`) to be installed — it reads
the real Apple product serial off SCSI VPD page 0x80 (systemd-udevd
already runs `scsi_id` as root for storage discovery; the rule just
captures that page into `ID_IOPENPOD_PRODUCT_SERIAL` without granting
raw-disk access) and publishes it via
`/dev/disk/by-id/ipod-<serial>`. Without that rule, Linux identity
resolution still falls back to firewire_guid/usb_pid only, and
`device/models.py`'s `USB_PID_TO_MODEL[0x1209] = ("iPod", "")` coarse
mapping (5th/5.5th gen share this PID) is **unchanged** in 1.67.0 — so
the fix only actually applies once the rule is installed.

Installed it live against the real device via `iopenpod
--linux-identity-status <mount>` (prints a self-contained, safe shell
script when the rule isn't installed yet — ran it verbatim, no
modifications). `state=ready`, `serial=8K6382K4V9S` afterward — this is
the same value already seen as one of the two inconsistent
`device_id` directories under `state/device_backups/` from the earlier
backup-retention work (`000A270015AE6188` was always the FireWire GUID
misread as a device identifier in earlier sessions; `8K6382K4V9S` was
the real Apple product serial all along, just not reliably readable
without this rule).

**Direct verification** (bypassing our own code entirely — called
`iopenpod.device.info.enrich()` straight against the real mounted
device): `model_family='iPod'`, `generation='5.5th Gen'`,
`model_number='MA450'`, every field's `_field_sources` value
`'linux_scsi'`, and `info.capabilities` already correctly populated
(`cover_art_formats` = formats 1028/1029, `supports_artwork=True`) —
with zero use of our monkeypatch. No "cached family conflicts with
live USB PID... using live USB identity" collapse anywhere in a full
plan-only sync run either (`grep`-checked the whole log for
conflict/coarse/warning text — nothing).

**Conclusion**: the identity-correction half of
`_capabilities_with_artwork_workaround` (the `if model_family ==
"iPod Video" or (== "iPod" and not generation)` block, and the
process-global `capabilities_for_family_gen` monkeypatch fallback) is
confirmed dead for this device now that the udev rule is installed.
Simplified and renamed to `_register_current_device` — it still has to
patch `get_current_device_for_path` (nothing else in this project's
headless path ever calls iopenpod's own `set_current_device()`, so that
part isn't a workaround, it's a real requirement), and still returns
`DeviceCapabilities(supports_artwork=False)` for a genuinely
unrecognized family as a safety net — but no longer hand-corrects
identity, and no longer mutates `capabilities_for_family_gen` globally
(scoped the unrecognized-family fallback to the returned object only).
Old tests asserting the coarse-placeholder-correction behavior replaced
with tests asserting the new, narrower behavior.

**Separately confirmed still needed**: read 1.67.0's actual
`artworkdb_chunks._write_mhii()` source directly — still only writes
`len(children)` for the mhii `childCount` field, still never appends
the real-iTunes-shaped third child (the missing-mhod-type-6 fix in
`_apply_missing_artwork_index_chunk_workaround`/
`_MHII_MISSING_INDEX_CHUNK`). Untouched, still wired into `plan_sync`.

**Also fixed while verifying**: the diagnostic plan-only sync used to
check all this was accidentally run without `--skip-backup` the first
time, and spent 10+ minutes on `BackupManager`'s full-file-hash pass
before being killed and restarted — that phase deliberately never
trusts a cache ("removable filesystems can retain coarse timestamps
across content changes"), so `FingerprintCache` (warm, last saved the
day before) never had a chance to help. Re-run with `--skip-backup`
(safe: a recent snapshot already existed, nothing had been written to
the device since) finished in 1m20s. Not a bug, just a reminder:
`--skip-backup` is the right flag for a read-only diagnostic run
against a device that hasn't changed.

**Follow-up**: `config/profiles/john.yaml`'s `device.match_by` switched
from `volume_label` to `serial` (`match_value: "8K6382K4V9S"`) now that
the serial is reliably readable — more robust than the volume label,
which has already changed once in this project's history (a real
iTunes resync renamed the volume to "VBOXUSER'S", see the ArtworkDB
mhii investigation above). `sync_orchestrator/device.py`'s
`find_matching_device` already supported `match_by: "serial"` before
this (checks `match_value in (info.serial, info.firewire_guid)`) — no
code change needed there, just the config value.

Our own udev trigger rule
(`services/sync-orchestrator/udev/99-ipod-music-stack.rules`, currently
**intentionally disabled** on this host —
`/etc/udev/rules.d/99-ipod-music-stack.rules.disabled` — so auto-sync
doesn't unmount the device mid-development) still matches on
`idVendor`/`idProduct` at the raw USB `ACTION=="add"` event, before a
block device exists — too early for `ID_IOPENPOD_PRODUCT_SERIAL` to be
available (that's only set once iopenpod's `61-iopenpod.rules` runs
against the later `SUBSYSTEM=="block", ENV{DEVTYPE}=="disk"` event).
Matching our trigger by serial instead — the more precise "the
best way to do automount/autosync" the user was after — would mean
switching our rule to match that same later block/disk event and
condition on `ENV{ID_IOPENPOD_PRODUCT_SERIAL}=="8K6382K4V9S"` instead
of PID. Because udev processes all matching rule files for one event
in filename-sorted order, and iopenpod's rule is numbered `61` (ours is
`99`), our rule would still see iopenpod's `ENV{}` assignment from
earlier in the same event pass — no race condition. Not yet
implemented (rule stays disabled by request); the design is confirmed
sound, just needs the actual rule text change once auto-sync is
re-enabled.

## Audiobook merge bitrate: fixed a real quality regression, plus two bugs found processing the next real batch

**2026-08-01/02.** The Kafka audiobook (`library/audiobooks/Franz
Kafka/The Trial`) sounded visibly worse than the source. Root cause:
`audiobook_manager.merge.merge_parts_to_m4b` hardcoded AAC output at a
flat `64k` regardless of source — Kafka's real source (still on
`olive:/mnt/storage/inprogress/Franz Kafka - The Trial/`) is 96kbps
stereo MP3, so it got knocked down to 64k AAC, a real bitrate cut plus
a lossy MP3→AAC generation.

Fixed per the user's stated policy — match source bitrate, capped at
what's sane for spoken word, go lossless above that — as
`merge.select_encoding` (`services/audiobook-manager/src/
audiobook_manager/merge.py`): AAC at ~source bitrate for anything
≤96kbps (iOpenPod's own "Spoken Word Bitrate" ceiling —
`gui/widgets/settingsPage.py`, options cap at 96kbps — the established
convention in this device's own tooling for where dialogue stops
benefiting from a higher bitrate), ALAC lossless above that. `bitrate`
param is now `None` by default (auto); an explicit value still forces
the old flat-lossy behavior.

**Rounding edge case, confirmed live**: the very first re-encode of
Kafka's real source produced a 4.1GB file (should have been ~400MB) —
`select_encoding` compared the *unrounded* probed bitrate against the
96kbps cap, and ffprobe's format-level `bit_rate` includes
container/frame overhead, so a real 96kbps MP3 probes at
96.005-96.006kbps — just over the cap, tripping the lossless branch for
a ~16x storage cost with zero quality benefit (the source has no more
information in that overhead to preserve). Fixed by rounding before
the comparison. Redone correctly: 398MB, AAC ~99kbps, all 12 chapters
intact, same Audible edition/tags as the original import (Stream
Readers, narrated by Daniel Brooks, B0H8SSMPHW).

**Real per-book judgment call, confirmed with the user rather than
guessed**: pulled 4 more books from `olive:/mnt/storage/audiobooks/`
(1984, Animal Farm, Marcus Aurelius Meditations, Neil Postman's
Amusing Ourselves to Death). 1984's source has 2 of 14 parts at
128kbps against the other 12 at ~56kbps (a mismatched rip) —
`select_encoding` takes the *max* across parts, so this pushed the
entire book to lossless ALAC (~2.9GB) to protect 2 outlier chapters.
User confirmed: keep it (safest, matches the policy literally) over
either lossy-downgrading the 2 outlier chapters to match the book
average, or a one-off two-pass merge. No code change from this —
documented so a future max()-vs-average design debate doesn't have to
re-derive it.

**Second real bug found in the same batch**: `merge_parts_to_m4b`'s
ffmpeg concat-list format (`file '{path}'`) had no escaping for a
literal single quote inside a path. Neil Postman's source has
`...Publisher's Introduction.mp3` — ffmpeg's concat demuxer closes the
quoted field at that apostrophe and fails to open the truncated path
(`ffmpeg exited 254`). Fixed with `_concat_escape` (close-quote,
escaped-quote, reopen-quote — `'\''`, the standard shell-quoting
technique, since ffmpeg's concat format follows the same convention).
Regression test added (`test_merge_parts_to_m4b_handles_apostrophe_in_
part_filename`).

Also extended `discover_parts` to accept `.m4a` sources (previously
`.mp3`-only) — Animal Farm's source is AAC `.m4a`, not MP3.

**Also found while staging**: `services/common/tests/test_config.py`'s
`test_example_profiles_load` still asserted `profiles["john"].device.
match_value == "JOHN'S IPOD"` — stale from before the serial-matching
switch documented above. Fixed to assert the real current values
(`match_by: serial`, `match_value: "8K6382K4V9S"`).

Full device sync after all 5 books were staged: `to_add=53
to_remove=1` (only the old degraded Kafka file, replaced by the new
one — exactly the expected removal), storage `+7.1GB -251MB`. Executed
clean with `--execute --allow-removals`.

## Podcast played-state round trip was completely broken for every episode — fixed

**2026-08-02.** User reported finishing several podcast episodes
on-device that weren't getting marked played or removed. Investigated
in plan mode (see the session transcript / `/home/john/.claude/plans/`
for the full write-up) rather than guessing at a fix.

Traced the pipeline: `sync_orchestrator/playstate.py`'s
`resolve_played_states` (called from every `plan_sync`, plan-only or
`--execute`) is the *only* place a device's `Play Counts` read-back
reaches `state/{profile}.sqlite`, which both `podcast-manager`'s local
deletion (`download.py`) and this project's own direct on-device
removal (`sync_orchestrator/podcast_removal.py` — matches a played
`EpisodeRecord` against on-device tracks by enclosure URL/title+album
and issues `REMOVE_FROM_IPOD` directly, independent of whether the
local file was already deleted) both key off.

Added temporary DEBUG-level logging to `resolve_played_states` (plus a
new `--debug` flag on `sync-orchestrator sync`) to make its
previously-silent per-track skip points observable, then re-ran
against the real connected device. Result: **8/8** podcast episodes
with real device activity failed at the same point —
`track_mapping.source_path_hint not in durations_by_path`. Root cause:
iopenpod's own mapping file (`iOpenPod.json`) stores
`source_path_hint` as a bare filename (e.g. `Open Sauce vs Better
Software Conference [2dc422bb-...].mp3`), not the absolute path our
`EpisodeRecord.local_path` uses — an exact full-path membership check
can never match against a bare filename. This wasn't a "a few
episodes" bug — with `local_path` stored absolute (as it must be, see
CLAUDE.md's `.resolve()` lore), the device-read-back path could not
have worked for *any* episode in this state. Note this is the
device-read-back path specifically: the 9 episodes already showing
`played=1` before this fix got there via the separate, working
Pocket-Casts-remote path (`podcast_manager.download._merged_played_state`
ORs in `record_remote_play_state`, independent of device read-back) —
not evidence the device path ever worked. Whether this is a
long-standing bug or a regression from whenever `local_path` was made
absolute (fixing the unrelated m3u8-path bug — see CLAUDE.md) isn't
established; not worth archaeology, the fix is the same either way.

Fixed by matching on filename instead of full path
(`durations_by_basename` lookup in `resolve_played_states`) — safe
because this project's own episode filenames already embed the Pocket
Casts episode UUID (`download.py`'s `_episode_path`), which is
globally unique, so no two episodes can collide on basename.
Regression test added
(`test_bare_filename_source_path_hint_matches_full_local_path`).

**Live-verified end to end, same session**: re-ran against the real
device with the fix — `8 episode(s) with new local play state
recorded` (matching the 8 debug-log entries exactly), and one episode
that had already crossed the 90% played threshold
(`playstate.PLAYED_THRESHOLD`) was immediately proposed for on-device
removal in the *same* plan (`podcast_removal.py` doesn't require a
separate podcast-manager pass first, unlike the local-file-deletion
side of this — it keys purely off the state db's `played` flag against
on-device tracks). Confirms the fix is complete and sufficient on its
own; no other link in the chain needed changing.

## Unsubscribed podcast shows were never pruned locally or from the device — fixed

**2026-08-02, same day.** User noticed two shows they'd unsubscribed
from on Pocket Casts (Dual Boot Diaries, Hard Drive) still had episodes
on the iPod. Traced the pipeline: unsubscribing correctly stops new
downloads (`list_subscriptions(token)` just won't return the show
again), but nothing ever pruned what was *already* downloaded — both
halves of the device sync plan (`sync_orchestrator/sync.py`'s
`_load_podcast_feeds` for additions, `podcast_removal.py`'s
`build_podcast_removal_items` for removals) read purely from the local
state db and local files, never checking current subscription status.
The only existing local-deletion path
(`podcast_manager/download.py`'s `delete_played_episodes`, inside
`sync_podcast`) only ever runs for a show that's still in
`subscriptions` — unsubscribing stops it from running for that show
ever again, so it can't be the thing that notices the unsubscribe
either. A genuinely missing feature, not a latent bug.

Added `EpisodeRecord.unsubscribed` (same schema-migration pattern as
`pending_push` — `common/state.py`'s `_migrate_episodes_columns`) and
`download.prune_unsubscribed_shows`: compares the account's *full,
unfiltered* subscription list against the state db's distinct
`podcast_uuid`s, deletes the local file and flags the row for any show
that's fallen out. Wired into both call sites that already fetch that
full list before narrowing it for a `--show`-restricted run
(`podcast_manager/cli.py::_cmd_sync`,
`music_stack_cli/orchestrate.py::run_sync`) — deliberately placed
*before* the narrowing, since a one-off `--show` restricted sync is a
per-run scope choice, not an unsubscribe signal, and using the narrowed
list there would have wrongly pruned every show outside that run's
scope.

**Cross-profile correctness, caught during design, not live**: podcast
audio files are shared/deduped across profiles by design (no profile
name in `_episode_path`/`show_dir` — see CLAUDE.md). If profile A
unsubscribes from a show profile B is still subscribed to, deleting the
shared file would silently break B. `prune_unsubscribed_shows` scans
sibling `*.sqlite` files in the same state directory before physically
deleting — skips the delete (but still sets *this* profile's own
`unsubscribed` flag, since its own device removal must still happen
independent of the file) if another profile's db still has a
non-unsubscribed row for that `podcast_uuid`. Not exercised for real
yet (only one real profile, "john", exists on this install — alice/bob
are templates), but the sharing behavior itself is real and documented,
so the bug this avoids is real too.

`podcast_removal.py`'s `build_podcast_removal_items` extended:
`if not (episode.played or episode.unsubscribed): continue`, with the
description reason showing whichever is more specific
(`"(unsubscribed)"` over `"(played)"` when both are true).

**Live-verified end to end**: `music-stack sync --source podcasts`
against the real account correctly identified and pruned exactly the
two shows the user named — `Pruned 10 episode(s) from 2 unsubscribed
show(s): Dual Boot Diaries, Hard Drive` — files gone from
`library/podcasts/`, state db rows flagged. Next device sync's plan showed 6 of those 10 as actually still present
on the device (1 Dual Boot Diaries + 5 Hard Drive — the other 4 Dual
Boot Diaries episodes were presumably already removed from the device
in an earlier played-episode removal cycle, before this fix existed;
`build_podcast_removal_items` correctly skips anything not currently
on-device) with `(unsubscribed)` labels, net -476MB; executed clean
with `--allow-removals`.

## `--allow-removals` didn't actually gate playlist removals — fixed

**2026-08-02, same day.** Investigating a stray on-device playlist
literally titled "Playlist" (user asked where it came from — never
traced to anything this project's own pipeline writes; likely a
leftover from this device's pre-project real-iTunes ownership, see the
"VBOXUSER'S" volume-rename history earlier in this file), a routine
plan-only run happened to show `playlists_to_remove=1` for the first
time this session. Went to identify which playlist before executing
(`cli.py`'s `_print_plan` prints `playlists_to_add`/`playlists_to_edit`
titles individually but never printed `playlists_to_remove` items at
all — only the count) and found something more serious while checking:
`_run_sync`'s hard safety gate
(`if planned.plan.to_remove and not args.allow_removals: return
_fail(...)`) only ever checked `plan.to_remove` (tracks).
`plan.playlists_to_remove` is a **separate** list on iopenpod's
`SyncPlan` (confirmed in `sync/contracts.py`) that was never covered —
a plain `--execute` with no `--allow-removals` would have silently
removed a real on-device playlist with zero review step, the exact
failure mode `docs/m6-ipod-headless-recommendation.md`'s "11 playlists
wiped" incident already scarred this project over, just for playlists
instead of tracks.

Confirmed live via a direct `plan_sync()` call (read-only, no
`--execute`) that the pending removal really was the mystery
"Playlist" (`playlist_id=5282529750801489828`, `master_flag=0` —
confirmed not the device's real master playlist) before doing anything
about it.

Fixed both gaps: `_print_plan` now prints each `playlists_to_remove`
item's title (same `Title`/`title`/`name` fallback chain as the
existing add/edit printing), and `_run_sync` gates
`plan.playlists_to_remove` behind `--allow-removals` exactly like
`plan.to_remove` already was. Regression tests added
(`test_print_plan_playlist_remove_prints_title_not_raw_dict`,
`test_run_sync_refuses_execute_when_playlist_removal_proposed_without_allow_removals`,
`test_run_sync_allows_execute_with_playlist_removal_when_allow_removals_set`)
— the latter two are the first direct unit tests of `_run_sync`'s
removal gate at all; it had only ever been exercised live/manually
before.

## Future: `music-stack sync`'s fetch side has no progress reporting

Noticed live (2026-08-15) running a first-time `music-stack sync
--profile ...` for a brand-new profile (`john-copy`, set up for a
newly-acquired second iPod — see the reformat/bootstrap notes around
this same date). The only output on stdout while it runs is
gamdl/fetcher-apple's raw `debug`-level structured log lines (full
JSON API responses per playlist/track dumped verbatim) — there's no
counter like "playlist 3/15" or "track 42/210", no ETA, nothing
structured to poll. Status had to be estimated by eyeballing which
playlist/track name last appeared in the log tail, same workaround
used for the same reason earlier this project (see "status and ETA"
checks during the big playlist-fetch session two sessions back).

This is the mirror-image gap to the one already fixed on the device
side (`sync-orchestrator: real progress reporting — shipped`, above) —
that shipped a `progress_callback` threaded through `plan_sync`/
`execute_sync` plus a throttled printer so a device sync shows `[scan]
3120/4416 — ...` instead of going silent. The fetch side
(`music_stack_cli/orchestrate.py`'s `run_sync`, looping fetchers +
`library-manager` + `podcast-manager`) has never gotten the same
treatment.

**Fix idea**: same shape as the device-side fix — a
`progress_callback` threaded through `run_sync` and each fetcher's
`fetch_playlist`, emitting one line per playlist/show started and
finished (`[fetch] apple_music "Chill" — 3/15 playlists, 42 tracks`),
with the existing throttled-printer pattern reused rather than
reinvented if per-track granularity turns out noisy. gamdl's own debug
logging would need to move behind a `--debug`-style flag (matching how
`sync-orchestrator sync --debug` already gates its own verbose trace)
rather than always being on, so the default output is the progress
line, not the raw API dump.

**Status**: not started — flagged for later, not blocking the
`john-copy` first-sync work in progress right now.

## `sync.transcode_format` was a complete no-op — fixed

**2026-08-15, same day.** Setting up `john-copy` (a second, smaller-capacity
iPod) as a fresh device with the same profile shape as `john.yaml` hit a
real "not enough space on iPod" failure (needs ~114GB, device has ~74GB
free). Trimmed playlists/podcasts/audiobooks first — barely helped
(111GB → 101.6GB), since most of that content already overlaps with
`external_library`, which is the real weight (nearly all of a 95GB
`MusicLibrary`, `mode: exclude` with only 2-3 artists carved out). Excluded
one more large artist (King Gizzard & The Lizard Wizard, 9.5GB) — still
nowhere near enough on its own. Decided to switch `john-copy.yaml`'s
`sync.transcode_format` from `alac` to `aac`, expecting iPod-side lossless
sources to get transcoded to lossy AAC instead, shrinking the total
significantly.

Went looking for where `transcode_format` actually influences
transcoding, to sanity-check the expected size drop before re-running the
device sync — and found nothing. `grep -rn transcode_format` across
`sync-orchestrator/src`, `library-manager/src`, and iopenpod itself: zero
hits outside `common/models.py`'s schema definition and test fixtures.
`plan_sync`'s `EngineOptions(...)` construction (`sync.py`) never set
`transcode_options` at all, so every sync — on `john`'s real device
included — has always run with iopenpod's own default
`TranscodeOptions()` (`prefer_lossy=False`), regardless of what any
profile's `transcode_format` said. The field has existed in the schema
since the project's early milestones and has never once actually done
anything.

(iopenpod itself has the real mechanism, confirmed by reading
`iopenpod/sync/transcoder.py`/`_formats.py`: native formats — mp3/m4a/
m4b/aac — play as-is with zero transcoding regardless of `prefer_lossy`;
only truly non-native lossless sources (flac/wav/aiff) get converted to
ALAC or, with `prefer_lossy=True`, AAC — and `prefer_lossy` also catches
already-native `.m4a`/`.m4b` files that are actually lossless-codec-in-
container (`bits_per_sample >= 16`), which is likely most of what's
inflating `MusicLibrary`'s per-track size here.)

**Fixed**: added `_transcode_options_for(profile) -> TranscodeOptions` in
`sync.py`, mapping `alac -> prefer_lossy=False` / `aac -> prefer_lossy=True`
via an explicit dict, raising `SyncError` for anything else (the schema
field is an unconstrained `str`, so a typo'd value must fail loud rather
than silently falling back to lossless). Wired into `plan_sync`'s
`EngineOptions(transcode_options=...)` — `execute_sync` needed no change,
it reuses `planned.options` from the same `plan_sync` call. Tests added:
`test_transcode_options_for_alac_prefers_lossless`,
`test_transcode_options_for_aac_prefers_lossy`,
`test_transcode_options_for_unsupported_format_raises`. Full suite (87
tests) passes.

Not yet live-verified how much this actually shrinks `john-copy`'s plan —
that's the next step. Worth knowing: this also silently changes behavior
for every *existing* profile using `transcode_format: alac` (including
`john`'s real device) — but since `alac` maps to `prefer_lossy=False`,
that's iopenpod's existing default, so no behavior change for anyone who
was already relying on the old (unwired) lossless-by-default behavior.
Only profiles that had `transcode_format: aac`/anything-non-`alac`
already set (none did, before `john-copy` today) would see a real
behavior change from this fix.

## iPod Classic 7th Gen: album art wrote correctly but never rendered on-screen — fixed

New primary device (2026-08-17, `john.yaml` updated to a new "iPod Classic
7th Gen" unit, model MC293, serial 8K13762U9ZS) synced fine — 6215 tracks
added, no errors — but the user reported no album art visible on-device,
including on the 48 tracks from an earlier isolated single-playlist test
sync that had (per the user) displayed art correctly before the full sync.

**First hypothesis, ruled out**: that this was the same "byte-correct
ArtworkDB, nothing renders" bug already fixed once before via
`_apply_missing_artwork_index_chunk_workaround()` (see the "5th/5.5th-gen
iPod Video artwork" entry above) simply not yet validated for the "iPod
Classic" family. Checked directly: parsed the real on-device `ArtworkDB`
(`iopenpod.artworkdb_parser.parser.parse_artworkdb`) — all 5794 entries
(100%) have the type-6 `mhod` workaround chunk. Decoded raw pixel bytes
directly from the on-device `.ithmb` files for both an old (preserved,
untouched by the full sync) and a newly-written track, across all 4
formats in `CLASSIC_COVER_ART_FORMATS` (1055/1060/1061/1068) — all
real, correct, undistorted album art. iTunesDB `artwork_id_ref`/
`has_artwork`/`artwork_count` links also correct. Tried a hard reset
(Menu+Center ~8s) per the old investigation's own suggestion — no change.
So: not a regression of the already-fixed bug, and not a device-side
render-cache issue either.

**Root cause found**: compared the real device's own `SysInfoExtended`
(`iPod_Control/Device/SysInfoExtended`, read live off the actual unit)
against what iopenpod wrote. The device's own `AlbumArt` array declares
**5** formats — 1069 (142x142, flagged with an `AssociatedFormat`/
`ExcludedFormats` pair no other entry has, strongly suggesting it's the
primary/Now-Playing format), 1055 (128x128), 1068 (128x128), 1060
(320x320), 1061 (**55x55**) — but iopenpod's `CLASSIC_COVER_ART_FORMATS`
(`device/artwork_presets.py`) only defines 4, entirely missing 1069 and
defining 1061 as **56x56** (off by one pixel each dimension).

Traced why the device's real capability list was never consulted:
`DeviceInfo.enrich()` (`device/info.py` ~line 1287) resolves
`info.artwork_formats` from the static per-family table
(`ithmb_formats_for_device` -> `capabilities_for_family_gen`) *first*,
and short-circuits — `if not info.artwork_formats and info.model_family`
— before ever trying to read the real `SysInfoExtended`. A dedicated
`_parse_sysinfo_artwork_formats()` function exists in that same module
but is dead code, never called from anywhere. So for every "iPod
Classic" family device (both this new primary *and* the second/80GB
device set up earlier this session — its artwork was never actually
visually confirmed either), the static table wins unconditionally and
the device's real, authoritative format list is silently ignored.

Separately, Apple's own `SysInfoExtended` XML for `AlbumArt` (and
`ImageSpecifications`/`ChapterImageSpecs`) is invalid plist — a `<key>`
element sits directly inside each `<array>`, immediately before each
format's `<dict>` — confirmed live: `plistlib.loads()` raises "unexpected
key" on the real file. iopenpod's regex fallback parser (used when
plistlib fails) doesn't attempt nested array-of-dicts extraction at all,
so even a project that *did* reach the SysInfoExtended-reading branch
would get nothing back for `AlbumArt`.

**Fix implemented (`sync_orchestrator/sync.py`)**: rather than patching
iopenpod's general-purpose plist parser to tolerate Apple's shape,
added a narrowly-scoped local workaround: `_sanitize_sysinfo_extended_plist()`
strips just the offending `<key>` elements that sit directly inside an
`<array>` immediately before a `<dict>` (regex-scoped to array bodies
only, confirmed via test not to touch ordinary top-level key/dict pairs),
then `_read_device_album_art_formats()` reads the real on-device
`SysInfoExtended`, sanitizes, and parses it with plistlib + iopenpod's
own `extract_image_formats()` (reused rather than reimplemented, so
dimension-field priority — RenderWidth/DisplayWidth/Width/width — stays
consistent with iopenpod's own logic). Wired into `_register_current_device()`:
overrides `info.artwork_formats` with the device-reported dict when one
parses successfully.

This is sufficient on its own — no other iopenpod code needed patching.
`resolve_cover_art_format_definitions_for_device()`
(`device/artwork.py`) already treats `device.artwork_formats` as
authoritative "observed" data when present, and its own
`_resolve_observed_format()` fallback chain (device-family static defs ->
global `ARTWORK_FORMATS_BY_ID` -> generic inferred RGB565 definition)
automatically produces the right shape once the *set of formats* is
right: format 1061 self-corrects to 55x55 via the generic fallback (its
entries in both the static table and the global registry are 56x56, so
neither matches and it falls through), and 1069 gets a generic-but-correct
`ArtworkFormat(1069, 142, 142, 284, "RGB565_LE", ...)` since it isn't in
either table at all. Verified this resolution chain directly against the
real device object before touching anything else.

5 new tests in `test_sync.py` (sanitizer preserves non-array key/dict
pairs; parses the real Apple shape; empty on missing file;
`_register_current_device` overrides the static table when a real
SysInfoExtended is present; leaves the static table alone when it's
not). Full suite (92, up from 87) passing.

**Not yet independently confirmed on-device** — the byte-level chain
was proven correct once before (the mhii-chunk case) and *still* didn't
render, so per that precedent this isn't being called fixed until a real
resync + the user physically confirms album art visible on the device's
own screen. Next step when picking this back up: real
`--execute --allow-removals` resync against the primary device, then
direct visual confirmation.

## Podcast episodes marked played were still synced to the iPod — fixed

While marking played state for a backlog of podcast episodes across
several shows (state db `played`/`played_up_to`, via `StateDB.update_play_state`),
the user asked why already-completed episodes kept getting synced to the
device anyway.

**Root cause**: `sync_orchestrator/sync.py`'s `_load_podcast_feeds()`
builds `iopenpod.podcasts.models.PodcastEpisode` objects straight from our
own `episodes` table, but never set `listened_override` (or `play_count`)
on them. iopenpod's own add/remove decision
(`podcasts/podcast_sync.py::_episode_was_listened()`) checks
`listened_override` first and otherwise falls back to `play_count` —
which our own loader also never set, and which upstream only ever
populates from **device-observed** play history
(`_update_episode_playback_from_track`, driven by an iPod track's own
play count read back on a previous sync). So our own Pocket-Casts-sourced
or manually-set `played` flag was completely invisible to the sync plan —
an episode only stopped being (re-)added once the device itself had
already recorded playing it once, regardless of what our state db said.

**Fix**: `_load_podcast_feeds()` now selects `played` too and sets
`listened_override=True if row["played"] else None` when constructing
each `PodcastEpisode`. Deliberately `None`, not `False`, for unplayed
episodes — `_update_episode_playback_from_track` treats an explicit
`False` as a *sticky* override and returns before ever recording new
on-device play data, which would permanently block real device-side play
tracking for any episode not already marked played through our own path.
`None` correctly means "defer to device/RSS-derived history" per
`PodcastEpisode.listened_override`'s own docstring.

2 new tests in `test_sync.py` (played episode gets `listened_override=True`;
unplayed stays `None`, not `False`). Full suite (94, up from 92) passing.

Adjacent, not-yet-fixed gaps noticed while reading this code (out of
scope for this fix, noting for later): `_load_podcast_feeds` also never
sets `pub_date` (defaults to 0.0 for every episode — with `fill_mode:
"newest"` sorting by `pub_date` descending, Python's stable sort means
ties keep DB row order rather than true chronological order, so "newest"
mode isn't reliably picking the actual newest episodes), and never
threads `profile.podcasts.max_episodes_per_show` into `PodcastFeed.episode_slots`
(silently stuck at the dataclass default of 3, not the profile's
configured value — e.g. `john.yaml` sets 5).

## Album art on iPod Classic 7th Gen: extensive byte-level investigation, three real fixes made, none were the actual root cause (superseded — see BREAKTHROUGH below)

Follow-up to "iPod Classic 7th Gen: album art wrote correctly but never
rendered on-screen — fixed" above. That entry was premature — the format
fix (adding the missing 1069 format, correcting 1061 to 55x55) never
actually got a confirmed "art now visible" from the user. When finally
checked, it still showed nothing. **This whole section is now
superseded**: the actual root cause (a total on-device `ArtworkDB`/
`.ithmb` size ceiling, unrelated to per-track data correctness) was
found later — see "BREAKTHROUGH" further down. Kept here in compressed
form because three real, still-in-place code fixes came out of this
investigation even though none of them explain the symptom.

**Three real fixes made and kept** (all byte-diff-verified against a
real iTunes-authored reference on the same physical device, all still
in `sync_orchestrator/sync.py` as of this writing):
1. `_filter_to_plain_album_art_formats()` — real iTunes only ever
   writes the 3 `AssociatedFormat == 0` formats per track (1055/1060/
   1061); the device's `SysInfoExtended` also declares 1068/1069, which
   real iTunes never uses (they're reserved for Now Playing chrome/
   video, not per-track thumbnails). Fixed `_read_device_album_art_formats()`
   to filter to just the 3.
2. `_write_mhfd_with_real_unk2()` — the ArtworkDB root header byte at
   offset 16 is `6` in real iTunes output for this device, not
   iopenpod's hardcoded `2` (stale comment claiming "1 until iTunes
   4.9, 2 after" doesn't hold for this iTunes version). Hardcoded to
   `6` rather than preserved-from-reference, since ours had already
   overwritten the device's real value once already.
3. `_write_mhit_with_duplicate_store_fields()` — real iTunesDB mirrors
   several already-correct fields into an undocumented "_2" duplicate
   block (`store_track_id_2` etc.) that our writer left zeroed. Mirrored
   the same way, but only for fields with a confirmed exact-mirror
   relationship — several other unknown fields in the same region were
   deliberately left alone (iopenpod's own code comments note most of
   them aren't "visibly promoted by the 2.0.1 MHIT loader" per prior
   reverse-engineering, i.e. likely inert either way).

**Every one of these was individually byte-diff-verified correct and
still produced zero visible change** — each was tested with a real
device sync after implementing it, none rendered art. That pattern
(correct data, never renders) is what eventually pointed away from
per-track data correctness entirely and toward a size-based cause.

**Other hypotheses tested and ruled out along the way** (kept as a
list — full narrative for each was cut, git history has it if needed):
non-square/high-resolution source images (resized a test playlist to
500x500, no change; on-device art is always re-encoded to fixed small
sizes regardless of source anyway); a stale/merged ArtworkDB entry vs.
a genuinely fresh one (forced remove+re-add of a test playlist,
confirmed fresh via commit log, still no art); root/sudo access for
extra SCSI-based device identification (confirmed real additional
access obtained, no change); two hard resets; an on-device Settings
audit (no album-art-related toggle found); a full crawl of every
relevant `TheRealSavi/iOpenPod` GitHub issue, open and closed (none
matched this exact symptom — closest precedent, #81/#69, had a
different root cause, a stray `1044` format entry, already absent from
this project's format list); a pure-vanilla-iopenpod-under-sudo test
(zero of this project's own code involved, real GUI, root access,
latest release) — still no art, ruling out every remaining lever this
project's own tooling could influence. A real upstream bug was filed
covering the format-set and mhfd findings above (2026-08-19,
`TheRealSavi/iOpenPod`, not yet resolved upstream). Foobar2000 and
MediaMonkey were researched as possible iopenpod replacements and both
ruled out on architecture alone (Windows-only, GUI-driven, no headless
path) — MediaMonkey's own forum has multiple independent, years-old
threads about this exact symptom class on iPod Classic, reinforcing
this as a device-level quirk rather than an iopenpod-specific bug.

**One real behavioral clue that turned out to matter**: on every sync
tried, the device visibly *attempts* to render something (a brief blank
moment, then a glitch, then falls back to a plain text summary) rather
than never engaging with artwork at all — a materially different signal
than silent non-engagement, and consistent with the eventual finding
(firmware hits a real ceiling partway through loading, rather than
never trying).

Superseded write-up ends here — see "BREAKTHROUGH (2026-08-20)" below
for the actual root cause and fix.

## BREAKTHROUGH (2026-08-20): album art renders correctly on a small, genuinely-fresh library

User's own hypothesis, prompted by the glitch behavior above and the
recollection that the very first-ever test (48 tracks, blank device)
had shown art correctly: maybe this was never a per-track data
correctness bug at all, but a **library scale** problem — either total
on-device data size, or a genuinely-fresh-vs-accumulated-state issue.

**Investigation**: checked actual on-disk artwork footprint on the
primary device (currently ~6,258 tracks): `iTunesDB` 16MB (well under
iopenpod's own documented 64MB cap for this device family — that cap
exists in `DeviceCapabilities.max_database_bytes` but is **never
actually enforced** anywhere in the sync/write code, confirmed by
grepping for it outside the GUI's storage-browser display — purely
cosmetic today). But `ArtworkDB` + `.ithmb` pixel files: **610-818MB**,
no documented cap anywhere for this. Confirmed iopenpod does not
compact `.ithmb` files on removal — reducing a device from ~6,000 to 88
tracks left the `.ithmb` files at ~488MB, essentially unchanged, since
removed tracks' pixel data is never reclaimed.

**Test setup**: rather than risk the primary 7th Gen device, used the
6th Gen/80GB unit already on hand for `john-copy` (chosen deliberately
— a more storage/RAM-constrained device family should surface a
size-related limit sooner). Created a new `config/profiles/testbed.yaml`
profile against the same physical device, then built a fully isolated
`--library-root` (a temp directory containing only the 48-track "Rise
Up" playlist, copied out of the real library) so `plan_sync`'s hardcoded
`library_root/music` inclusion (see below) wouldn't drag in the whole
shared library regardless of playlist scope. Did a real `mkfs.vfat -F 32`
reformat of the device (confirmed the right one first via
`udevadm info`'s `ID_SERIAL_SHORT`/`ID_IOPENPOD_PRODUCT_SERIAL`, matching
`john-copy.yaml`'s configured serial) to guarantee genuinely-fresh
`.ithmb` files, not just a lower track count. A completely blank
filesystem needed manual bootstrapping to be recognized at all:
`iPod_Control/` must exist as a directory (`sync_orchestrator/device.py`'s
own candidate check, stricter than iopenpod's, additionally requires
`iPod_Control/Device/SysInfo` to be a real file — recreated from this
exact device's own pre-wipe SysInfo dump, captured earlier the same
session) and `iPod_Control/iTunes/iTunesDB` must be a valid (if empty)
mhbd file for `resolve_itdb_path`/the parser to accept at all (built via
`iopenpod.itunesdb_writer.mhbd_writer.write_mhbd(tracks=[])` directly).
`device.match_by` had to switch from `serial` to `volume_label`
temporarily too — serial matching needs cached/enriched device data that
doesn't exist on a truly blank filesystem yet.

**Real, previously-undocumented architecture finding along the way**:
`sync.py`'s `plan_sync()` hardcodes `library_root / "music"` as a
pc_folder unconditionally (line ~691) — a profile's `playlists:` list
only controls what gets *fetched*/what `.m3u8` files exist, not what
raw files get scanned for the device sync. Every profile's device sync
has always pulled in the **entire shared library**, not just that
profile's own configured playlists — confirmed live (narrowing
`testbed`'s scope via profile config alone left 1,887 tracks on device,
not ~50, until the isolated `--library-root` was used instead). This
explains why every profile's real-world sync this session always
involved thousands of tracks regardless of how few playlists a given
profile actually lists.

**Result**: fresh 48-track sync onto the reformatted device — on-disk
artwork footprint dropped to **11MB** (from 488-818MB) — and **album art
displayed correctly on-device**, confirmed by the user. This is the
first successful on-device album art render anywhere in this entire
multi-session investigation.

**Status: root cause narrowed to library scale (data size and/or
freshness), not per-track data correctness.** Every per-track byte-level
fix made this session (format-set filtering, mhfd header byte, iTunesDB
"_2" duplicate fields) was still present/active in this successful test
— none of them were reverted — so they were likely either genuinely
correct-and-necessary all along (just not individually sufficient) or
inert; can't fully disentangle which without more testing. **Not yet
determined**: whether the fix is really about (a) total `.ithmb`/
`ArtworkDB` byte size crossing some real firmware ceiling somewhere
between 11MB and 488MB, (b) genuinely-fresh-vs-accumulated on-device
state (the reformat, independent of final size), or (c) something else
entirely correlated with both. Next step: scale the track count up
gradually on this same disposable test device (not the primary 7th Gen
unit) to find the actual threshold before attempting anything on the
primary device, given a primary-device reformat costs ~2-3 hours to
rebuild and this device family's constraints may not transfer 1:1 to
7th Gen's larger capacity/possibly-different RAM.

**2026-08-20: a second hypothesis to disentangle alongside pure size —
orphaned/unlinked `.ithmb` data, not scale itself.** User's framing:
since `.ithmb` files are confirmed never compacted on removal (see
above — 488MB survived a 6,000→88 track reduction), maybe the *real*
primary-device trigger isn't "6,000 tracks" in the abstract but the
messy history of dozens of add/remove/resync cycles this session
(real iTunes syncs, mapping resets, force remove+re-add tests, etc.)
each leaving unreclaimed orphaned bytes behind — i.e. staleness/
corruption from churn, not sheer final size.

This test's growth methodology (48 → 500 → 1,882, pure adds only, zero
removals) happens to already isolate this cleanly: if album art
survives all the way to filling the device or exhausting the available
library with **no removals ever performed**, that points at orphaned-
data/history as the primary device's real problem, not track count
itself. If it breaks at some number well before that despite the clean
growth-only history, that's a genuine size/count ceiling independent of
history. Continuing the scale-up test with this framing in mind.

**2026-08-20: scale-up results — orphaned-data hypothesis ruled out,
real size/count ceiling confirmed.** Grew the disposable test device via
pure adds only, zero removals at any point, checking album art at each
step:

| Tracks on device | Artwork dir size | Album art |
|---|---|---|
| 48 (fresh reformat) | 11MB | **works** |
| 495 | 110MB | **works** |
| 1,842 | 397MB | **fails** — same blank→glitch→text-fallback pattern as every primary-device failure this whole investigation |

This is decisive against the "orphaned/unlinked `.ithmb` data" theory —
this device's entire history was clean, additive growth with no
removals ever performed, yet it still failed. That leaves a genuine
size/count ceiling somewhere between 110MB and 397MB (not simply
"6,000 tracks" — this failed at under a third of the primary device's
track count).

Full breakdown of the Artwork/ directory at the 397MB failing
checkpoint: `ArtworkDB` 1.4MB, `iTunesDB` 8.4MB (a separate file, not
counted in the 397MB), `F1055_1/_2.ithmb` (128x128) 32MB+24MB,
`F1061_1.ithmb` (55x55) 10MB, and **`F1060_1` through `F1060_11.ithmb`
(320x320) — 11 separate files**, each capped at exactly 32MB (a known,
documented Apple per-`.ithmb`-file limit, not a bug) except the last
partial one. Every `.ithmb` file observed anywhere in this investigation
has obeyed this same 32MB-per-file ceiling, so file count per format and
total byte count are tightly coupled (file count ≈ total bytes ÷ 32MB)
— this data alone can't distinguish "too many bytes" from "too many
chunked files" as the actual mechanism, since they move together given
a fixed chunk size. Didn't capture the exact per-format file count at
the last *working* (110MB) checkpoint, only the total size, so a future
test varying file-count independently of byte-total (e.g. many tiny
unique images vs. few large ones) would be needed to fully separate
these two framings if it matters.

**Status**: real ceiling confirmed to exist somewhere in [110MB, 397MB]
for this 6th Gen/80GB device. Next: binary-search further within that
range on the same disposable device to narrow the actual threshold.

**2026-08-20: binary search, rounds 1-6.** Switched to a much faster
reset method between checkpoints: rather than a full `mkfs.vfat`
reformat + rebuilding `iPod_Control/Device/SysInfo` from scratch each
time, just delete `iPod_Control/Artwork/` and `iPod_Control/iTunes/
iTunesDB` (+ `.backup`/`Play Counts`) directly and rewrite a fresh empty
`iTunesDB` via `iopenpod.itunesdb_writer.mhbd_writer.write_mhbd(tracks=[])`
— `SysInfo` survives untouched so device identity/matching needs no
rebuilding either. Confirmed equivalent to a full reformat for this
test's purposes. All checkpoints below are fresh-wipe, no-removals,
same methodology as the scale-up test above:

| Round | Tracks | Artwork size | Result |
|---|---|---|---|
| 1 | 1,086 | 236MB | works |
| 2 | 1,422 | 301MB | works |
| 3 | 1,619 | 340MB | **fails** |
| 4 | 1,500 | 316MB | works |
| 5 | 1,540 | 324MB | works |
| 6 | 1,550 | 326MB | works |

Byte-per-track ratio stayed remarkably consistent throughout
(~0.215-0.222 MB/track), suggesting the ceiling scales fairly linearly
with track count for this library's typical art-sharing/dedup ratio —
final narrowed range: **[326MB, 340MB]**.

Structural analysis of the failing 340MB `ArtworkDB` (round 3, user's
request) found nothing besides raw byte size that could explain it: no
duplicate entries, `img_id` range resets cleanly each checkpoint, no
unbounded growth across wipes. `mhfd` header fields `unk4`/`unk5`/
`unk9`/`unk10` (normally preserved per-device magic/GUID values) are
zero in every checkpoint here since a full `ArtworkDB` wipe leaves
nothing to preserve from — a real methodological difference from
accumulated-history syncs, but constant across pass and fail
checkpoints alike, so not the differentiator.

**Dedup efficiency check (user's request)**: at the 316MB/1,500-track
checkpoint, 1,352 unique images referenced across 1,499 artwork entries,
versus 1,344 distinct (artist, album) pairs actually present in that
test library — dedup is running at ~99% efficiency for this test, i.e.
already essentially "one image per album." Important caveat for
interpreting the whole binary search: this test library is a *random
sample of individual tracks* scattered across ~1,344 different albums,
so most albums only contribute 1 track — there's naturally almost
nothing to dedupe. The real primary library (built from whole
playlists/albums, not random individual tracks) has a genuinely better
real-world dedup ratio, confirmed earlier this session: 1,983 unique
images for 5,958 artwork entries (~3:1) on the full ~6,258-track
library, versus ~1.1:1 in this random-sample test. **This means the
MB-per-track ratio measured in this whole binary search is pessimistic
for the real library** — real playlists/albums should pack meaningfully
more tracks into the same artwork footprint than this test's numbers
suggest, since real-world content dedupes far better than a random
individual-track sample does.

**Planned follow-up (not yet done)**: build a test library with a
*fixed* number of unique albums (~1,500) but many more total tracks
(full albums, not single random tracks) to directly test whether the
ceiling tracks unique-image footprint or raw entry count regardless of
dedup.

Confirmed on-device format resolutions directly from a saved
`ArtworkDB` during round 5: `F1055` 128x128 (32,768 bytes), `F1060`
320x320 (204,800 bytes), `F1061` 55x55 (6,050 bytes) — ~243KB per
unique image across all three sizes combined, matching real iTunes'
own set exactly. (Rockbox was considered as a hypothetical alternative
firmware mid-session and set aside as out of scope — its album art
mechanism is architecturally unrelated to Apple's `ArtworkDB` anyway,
so this bug wouldn't apply there regardless.)

**2026-08-20: growth *rate* vs. total size — tested, inconclusive,
treated as ruled out.** Every checkpoint above was a fresh wipe
followed by a single-shot full sync (0 → N tracks in one commit),
confounding "total on-device size" with "amount added in a single
sync." Tested by adding incrementally (no wipe) from the confirmed-
working 1,550-track/326MB state, aiming to land at the already-known-
failing 1,619-track/340MB point without a fresh wipe. The test
overshot its target — landed at 1,715 tracks/364MB instead, past the
known failure boundary — and the result was **no album art**. Since
364MB is past the boundary either way, this doesn't cleanly distinguish
the two hypotheses (a clean disambiguating test was never re-run), but
it's mild evidence against growth-rate mattering — a smaller
incremental delta still failed at roughly the same total-size boundary.
User accepted this reasoning and declared growth-rate ruled out;
binary search paused at **[326MB, 340MB]** for this device, not
resumed since (see "Investigation paused" near the end of this file).

## RESOLVED: a single podcast episode's played state reverted unexpectedly

**Status**: root-caused and fixed later the same session — see "Real
root cause of every 'impossible' Malala revert: sync-orchestrator's
venv silently ran stale `common` code" below. Cause was
`sync-orchestrator`'s standalone `.venv` running a stale, non-editable
build of `common` that still had the pre-fix raw-overwrite
`update_play_state`; `uv sync --reinstall-package common` is now the
documented fix/prevention. Left below verbatim as the original
investigation notes.

Observed live (2026-08-18): "Malala Yousafzai..." (The Louis Theroux
Podcast) was manually marked played via `StateDB.update_play_state`,
confirmed committed at the time, but later showed `played=0` again after
running `music-stack sync --source podcasts` a couple more times.
Several other episodes marked played in the exact same batch (Gary
Stevenson, Olivia Wilde, David Byrne) correctly stayed played across the
same runs, so this isn't a uniform reset bug. Checked the one call site
that refreshes played state from Pocket Casts
(`podcast_manager/download.py::sync_podcast`, via
`_merged_played_state`/`record_remote_play_state`) — it's correctly
OR-merged (local `played=True` should never be downgraded by a remote
`False`), so that's not directly it, at least not obviously. Not
root-caused; worth a real repro (mark one episode played, run the fetch
step step-by-step with logging/breakpoints around `record_episode` and
`record_remote_play_state`) before trusting manual play-state marks to
survive repeated podcast fetch runs.

## Device-observed plays never actually reached Pocket Casts — the push step existed but was never wired in

Follow-up to the Malala Yousafzai played-state mystery above: user asked
to extend the logic so a play observed on the iPod actually gets marked
played on Pocket Casts too. Turned out this already existed in code —
`podcast-manager push-play-status` reads `pending_push=1` rows (set by
sync-orchestrator's device read-back, `playstate.py`, or a manual
`update_play_state` call) and pushes them via Pocket Casts'
`update_episode_status` API — but it was only ever a standalone CLI
command. Neither `music-stack sync` (`orchestrate.py::run_sync`) nor
`sync-orchestrator sync` ever called it automatically, so real on-device
listening progress never reached Pocket Casts unless someone remembered
to run that command by hand — which nobody ever had, in this project's
history. **Fixed**: extracted the push logic into a reusable
`podcast_manager.download.push_pending_play_status()` (the CLI command
now just calls it), and wired it into `run_sync`'s podcast handling,
right after authenticating and *before* `prune_unsubscribed_shows`/
`sync_shows` — so a normal `music-stack sync --source podcasts` run now
round-trips properly: push what the device/local db already confirmed,
then pull Pocket Casts' now-current state.

**A second, related bug found and fixed while root-causing the Malala
revert**: `StateDB.update_play_state()` (the function sync-orchestrator's
device read-back calls) did a raw overwrite, unlike
`record_remote_play_state()` (which correctly OR-merges so a remote read
can never downgrade a locally-confirmed play). Confirmed live: this let
stray/minor on-device play activity — e.g. a few incidental seconds well
under `PLAYED_THRESHOLD` — silently clobber an episode already marked
played through Pocket Casts or a manual override back to unplayed on
every subsequent device sync, before the very `pending_push` flag that
should have gotten it pushed to Pocket Casts ever had a chance. This is
the most likely (though not independently, byte-level confirmed) real
explanation for Malala Yousafzai's episode repeatedly reverting earlier
today, and would have quietly undermined the push-wiring fix above even
once it existed — a device sync could keep re-clobbering a play before
it ever got pushed. `update_play_state` now OR-merges the same way
`record_remote_play_state` does.

6 new tests across `common`/`podcast-manager`/`music-stack-cli` (never-
downgrades merge behavior; push-and-clear; skip-when-nothing-pending;
one-failure-doesn't-abort-the-batch, with the failed episode correctly
staying `pending_push=True` for retry; push-runs-before-prune wiring).
Also fixed two stale hardcoded assertions in `common`'s
`test_example_profiles_load` (profile set now includes `john-copy`;
`john`'s device serial was updated to the new primary iPod earlier this
session) — unrelated to this fix, just surfaced by running the full
suite. Full workspace suite: 272 passing (up from 265).

## Device sync now automatically pushes play status to Pocket Casts afterward

Follow-up to the push-wiring fix above: that fix made `music-stack sync
--source podcasts` push pending play-state updates automatically, but a
device sync (`sync-orchestrator sync`) itself still didn't trigger
anything afterward — a human still had to remember to separately run a
podcast sync to actually get real on-device listening progress to Pocket
Casts. User asked for this to happen automatically right after a device
sync, so Pocket Casts stays as current as possible.

**Fixed**: added `_maybe_push_play_status()` to `sync_orchestrator/cli.py`,
called right after a successful `--execute` run (both the plain `sync`
command and the udev-triggered `auto-sync` command share `_run_sync`, so
this covers both automatically). Gated on `profile.sync.push_play_status_back`
— which, like `transcode_format` before it, existed in the schema this
whole time but was never actually read by anything until now — and on
`planned.play_states_updated > 0` (no point spawning a subprocess when
the device read-back found nothing new). Shells out to `music-stack sync
--source podcasts` rather than importing `podcast_manager` in-process,
matching the exact reasoning `_maybe_pre_fetch` already established for
keeping sync-orchestrator's dependency tree isolated from
music-stack-cli's heavier one. Best-effort: a failed push warns but
doesn't fail an otherwise-successful device sync — `pending_push` stays
set, so the next podcast sync just retries it.

`_run_sync`'s signature now takes explicit `profile_path`/`config_root`
keyword args (previously derived nowhere, since only `_maybe_pre_fetch`
needed them, and only auto-sync ever called that): the plain `sync`
command derives them from `--profile`'s own path (standard
`config/profiles/<name>.yaml` layout, `config_root` is two parents up);
`auto-sync` already had both from its own profile-directory scan.

4 new tests (skip when nothing updated; skip when disabled in profile;
correct subprocess invocation — scoped to `--source podcasts` only, not
music sources too; failed push warns without raising). sync-orchestrator
suite: 98 passing (up from 94) — note this suite is intentionally *not*
part of the root workspace's `uv run pytest` (see `pyproject.toml`'s
`testpaths` comment — same "keep iopenpod's dependency tree isolated"
reasoning), so it must be run separately from
`services/sync-orchestrator`.

## Push-after-sync gate fixed: check real pending_push count, not just this run's delta

Follow-up to the auto-push-after-device-sync feature above. Live-tested
it (2026-08-18) with a genuinely-played episode ("Open Sauce vs Better
Software Conference") and found the gate was wrong: `_maybe_push_play_status`
was keyed on `planned.play_states_updated` (this run's own device-read-
back delta) rather than the state db's actual `pending_push` count. A
device's "recent" play delta is effectively consumed the moment a commit
reads it -- it doesn't persist for a later run to re-detect. So anything
already pending from *before* this run (a manual override, or an earlier
device sync whose resulting push got lost to a race with a concurrent
podcast fetch -- confirmed this happened live, to both Open Sauce and a
second episode, Malala Yousafzai's) would silently never get pushed by a
device sync that itself found nothing new on the device: the real signal
was gone, and the stale `pending_push=1` flag just sat there unflushed.
Fixed: now checks `StateDB.list_episodes_pending_push()`'s real count
directly. 5 tests (was 4) rewritten around real state-db fixtures rather
than a passed-in int. sync-orchestrator suite: 99 passing.

## Play-status push subprocess silently failed: relative project-dir default only worked from repo root

Live-testing the auto-push feature (2026-08-18) with a real on-device
play surfaced the actual reason Open Sauce/Malala's pushes kept
disappearing: `--music-stack-project-dir` defaulted to the plain string
`"services/music-stack-cli"`, resolved relative to whatever the CWD
happened to be when `sync-orchestrator sync` was invoked. Every manual
invocation this session was from inside `services/sync-orchestrator/`
(not the repo root), where that relative path resolves to nothing --
`uv run --project services/music-stack-cli music-stack sync ...` failed
with "No such file or directory", *silently*, since the device sync
itself still reports PASS and only prints a WARNING for the push
failure -- easy to miss in a long log, and exactly what happened. Only
`_maybe_pre_fetch` (auto-sync's pre-fetch step, using the same relative
default) never hit this in practice because auto-sync's systemd unit
always sets its WorkingDirectory to the repo root.

**Fixed**: added `_default_music_stack_project_dir()`, deriving an
absolute path from `cli.py`'s own installed location
(`Path(__file__).resolve().parents[3] / "music-stack-cli"`) rather than
a CWD-relative guess -- immune to wherever the command is actually
invoked from. Used as the default for both `sync` and `auto-sync`'s
`--music-stack-project-dir`. 1 new regression test. sync-orchestrator
suite: 100 passing.

Once this was fixed, a real device sync correctly detected the on-device
play (`recent_playcount=1`, confirmed present via direct on-device
inspection -- an earlier concern that the ephemeral "recent" delta gets
permanently consumed after one read turned out to be unfounded; it
persisted across multiple reads/commits in this same investigation) and
successfully pushed 3 pending play-state updates (Open Sauce, Malala,
and this run's own detection) to Pocket Casts in the same run.

## RESOLVED: Pocket Casts push kept losing already-confirmed plays — root-caused, partially fixed, one real bug still open

**Status**: the "real, still-open bug" flagged below (ambiguous
`recent_playcount`/position signal on completed-but-low-position plays)
was fixed later the same session — see "Permanent fix: play_count_1
fallback closes the 'undetectable completed play' gap for good" below.
`playstate.py` now also checks the cumulative, never-reset
`play_count_1` counter as a fallback, without regressing the existing
partial-play test this entry's own fix attempt broke. Left below
verbatim as the original investigation notes.

User confirmed on-device UI showed both episodes ("Open Sauce vs Better
Software Conference", Malala Yousafzai's Louis Theroux episode) as
played, but Pocket Casts still showed them unplayed even after the
auto-push feature ran (with the CWD fix from the entry above applied,
push genuinely reported success: "Pushed play status to Pocket Casts
for 3 episode(s)").

**Root cause, confirmed**: `update_episode_status()` itself works
correctly — verified with a direct, isolated API call (before: real
Pocket Casts state `played=False`; after: `played=True`, immediate
re-read confirms it stuck). The push mechanism pushes whatever value is
*currently in the local row* at push time, correctly. The problem: two
rows still had a **stale** `pending_push=1` flag left over from the
earlier session-long investigation (a race between a device sync and a
concurrently-running podcast fetch clobbered their `played` value back
to `False`, well before the push-wiring/OR-merge fixes above existed) —
`played` was `False` on those rows, so the "successful" push correctly
sent `False`, overwriting Pocket Casts' state with the wrong value.
This is stale corrupted data from earlier in the same investigation, not
a new instance of a live bug in the current, already-fixed code path.

**Fixed for real**: pushed `played=True` directly via `update_episode_status`
for both episodes (confirmed via `list_episode_states` immediately after
each), then reconciled the local db to match via
`StateDB.update_play_state`. Both now correctly show played on Pocket
Casts and locally.

**A real, still-open bug found and NOT fixed**: while investigating
Malala's case (real device data: `play_count_1=2` but `bookmark_time=572ms`
— barely into a 66-minute episode), tried changing `playstate.py`'s
duration-known branch to also trust `recent_playcount > 0` regardless of
position (theory: iPod resets bookmark to near-zero after a track
naturally completes). **This broke an existing, deliberately-designed
test** (`test_partial_play_with_known_duration_is_in_progress_not_played`,
`recent_playcount=1` + 16.7% position must stay "in progress," not
"played") — meaning `recent_playcount` can genuinely be nonzero for a
real partial play too, not just full completions, so trusting it
unconditionally would cause false positives. Reverted the change; full
suite passes again (100 in sync-orchestrator).

This means Malala's real device signal is **genuinely ambiguous** under
this project's own already-vetted logic — it's not possible to tell,
from `play_count_1`/`recent_playcount`/`bookmark_time` alone, whether
"play_count incremented but position is low" means "finished, then
position reset" or "briefly sampled/skipped without finishing." Fixing
this properly needs a device with a known, controlled play/skip/finish
sequence to observe real `Play Counts` file behavior directly (matching
the existing code comment's own caveat: "not independently verified
against raw device Play Counts data"). Not attempted further this
session — flagging for whoever picks this up next, since it directly
affects whether `resolve_played_states` can be trusted to correctly
detect a genuine completion when the position signal is misleading.

## Real root cause of every "impossible" Malala revert: sync-orchestrator's venv silently ran stale `common` code

After three separate "corrections" to Malala's played state all silently
reverted despite `update_play_state`'s OR-merge fix looking correct on
paper (and covered by passing tests), got a `--debug` trace showing
`resolve_played_states` computing `played=False` for her (real: her
`bookmark_time=572ms` vs a known 3963s duration — position-based check
correctly says "not played by position"), followed by the local db
actually reverting to that value. Checked `inspect.getsource(StateDB
.update_play_state)` from *inside a real sync-orchestrator run* — it
was still the **old, pre-fix, raw-overwrite version**, despite the fix
being correctly committed, present in `services/common/src/common/state.py`
on disk, and covered by passing tests in `services/common`'s own test
suite.

**Root cause**: `services/sync-orchestrator` has its own standalone
`.venv`, deliberately isolated from the main workspace (kept separate so
its `iopenpod` dependency tree never merges with anything else — see
`_maybe_pre_fetch`'s docstring). Its `common` path dependency
(`[tool.uv.sources] common = { path = "../common" }`) was installed
**non-editable** (`direct_url.json`: `"editable":false`) — a real,
immutable snapshot copied at install time, not a live link to the
source. The main workspace's own root `.venv` (used by `music-stack-cli`/
`podcast-manager`/etc.) has `common` installed **editable** (`true`) —
so every OTHER service picks up `common` edits immediately, and only
`sync-orchestrator` silently doesn't. `uv sync` alone did not refresh
it either (reported "Resolved... Checked..." with no actual reinstall);
`uv sync --reinstall-package common` was required to force a real
rebuild+reinstall.

This explains the exact asymmetry that made this so hard to pin down:
Open Sauce's fix "stuck" (her `bookmark_time` had reset to exactly 0,
so `resolve_played_states`' very first check —
`if not recent_playcount and not bookmark_time_ms: continue` — skipped
her entirely, never calling the stale `update_play_state` at all) while
Malala's nonzero `bookmark_time=572` meant she was *always* re-evaluated
and always hit the broken raw-overwrite path.

**Action for future sessions**: after editing anything in
`services/common` (or any other path-dependency service), run
`cd services/sync-orchestrator && uv sync --reinstall-package common`
(or `--reinstall` for a broader refresh) before trusting a
sync-orchestrator run to reflect the change — a plain `uv sync` is not
sufficient and gives no warning that it didn't actually update anything.
Worth checking whether this same gotcha affects any other standalone-
venv service in this project (fetcher-spotify is the other one
documented as deliberately standalone) before relying on a similar fix
there without re-verifying.

## Permanent fix: play_count_1 fallback closes the "undetectable completed play" gap for good

Direct answer to "how do we ensure this doesn't happen in the future?"
after repeatedly having to manually reconcile fully-completed episodes
(Open Sauce, Malala, then Mike D and Marina Abramovic — same pattern
each time) that `resolve_played_states` couldn't detect.

**Fixed properly this time**: `playstate.py` now also checks
`track["play_count_1"]` — iopenpod's own documented "new cumulative"
play counter (`itunesdb_parser/playcounts.py`), which is incremented on
every merge and **never reset** — as an unconditional fallback signal.
Previously the function only ever looked at `recent_playcount`/
`bookmark_time`, both sourced from the ephemeral per-sync Play Counts
delta file, which iopenpod itself zeroes out once merged
(`playcounts.py` line 258, `track["play_count_2"] = 0`) — so a genuine
completed play became permanently invisible to this function the moment
its own triggering commit had already consumed the delta, with no
further signal left to catch it by. `play_count_1 > 0` now always forces
`played = True`, regardless of what the position/delta check computed —
one-directional only (never downgrades an already-True result), so it
can't reintroduce the earlier reverted `recent_playcount`-based bug
(that used the ephemeral delta field, which genuinely can be nonzero for
a real partial play too, per the still-passing `test_partial_play_with_
known_duration_is_in_progress_not_played`; `play_count_1` is a different,
persistent field with different semantics — "has ever completed" not
"active this session").

2 new tests (cumulative count overrides a fully-consumed delta;
zero cumulative count never downgrades an existing partial-play result).
sync-orchestrator suite: 102 passing. This is purely additive to
`resolve_played_states`'s own logic (no `common` involvement), so no
`uv sync --reinstall-package` gotcha applies here — this is a genuine
fix from the moment it's on disk in sync-orchestrator's own source.

## Real per-show episode caps + RSS-sourced episode metadata — shipped

Two gaps addressed together (same code path, same schema migration):

**`max_episodes_per_show` now actually caps the local library.**
`sync_podcast()`'s `candidates[:max_episodes_per_show]` only ever
limited new *additions* per run — nothing pruned the accumulated
backlog, so shows built up well past their configured limit (confirmed
live: Timothy Keller Sermons had 17 local episodes against a limit of
5). Fixed by extending the existing `delete_played_episodes and
sync_unplayed_only` pruning block in `podcast_manager/download.py`: an
episode that's aged out of this run's top-N "wanted" window (still
eligible per Pocket Casts, just no longer in the newest/next N) now
gets its local file deleted immediately, same as an already-played one.
Gated behind the exact same condition as the existing played-based
pruning, so an explicit archival profile (`sync_unplayed_only=False`)
is never touched. Live-verified: raising `john.yaml`'s
`max_episodes_per_show` to 10 and re-running pruned Timothy Keller from
17 -> 10 in one run.

Separately, `sync_orchestrator/sync.py::_load_podcast_feeds()` built
every `PodcastFeed` with no `episode_slots`/`fill_mode` at all, silently
running on iopenpod's dataclass default (`episode_slots=3`) instead of
the profile's real value — same "config field exists but nothing reads
it" shape as `transcode_format`/`push_play_status_back` found earlier
today. Now threads `profile.podcasts.max_episodes_per_show` and
`profile.podcasts.fill_modes` through (the function gained a `profile`
parameter). Live-verified via a real device sync after the fetch-side
fix above.

**Episode description, episode number, season number, and published
date are now stored.** Pocket Casts' own API doesn't expose any of these
per-episode (confirmed live against `/podcast/full/` — only title/url/
duration/published/slug/file_type). New module
`podcast_manager/rss.py`: `resolve_feed_url()` looks up a show's real
RSS feed via Apple's public, key-free iTunes Search API (matched by
title+author; no feed/RSS URL of any kind is exposed by Pocket Casts
itself); `fetch_rss_episodes()` fetches and parses it with stdlib
`xml.etree.ElementTree` (no new dependency). Matched to a Pocket Casts
`FullEpisode` by enclosure URL — confirmed live these are byte-identical
between Pocket Casts and the real RSS feed. Best-effort throughout: an
unresolvable show or a malformed feed degrades to blank/`None`
metadata, never blocks a sync (same resilience pattern already used for
per-episode download failures). `episode_number`/`season_number` are
genuinely optional — not every podcast tags them (confirmed live: The
Standup with ThePrimeagen doesn't). `published_at` falls back to Pocket
Casts' own `published` field when RSS enrichment doesn't cover a given
episode (feed unresolved, or the item isn't in the feed's own returned
window) — always populated one way or the other. Four new
`EpisodeRecord`/`episodes` columns, same migration pattern as
`unsubscribed`/`pending_push` before them. Fixed the adjacent `pub_date`
gap while touching this code anyway: `_load_podcast_feeds` never set
`PodcastEpisode.pub_date` (stayed `0.0` for every episode, undermining
`fill_mode="newest"`'s own sort reliability) — now parsed from
`published_at` (handles both RSS's RFC 822 `pubDate` and Pocket Casts'
ISO format).

Planned properly first (see the ancient-orbiting-hopcroft plan) —
spiked both the iTunes Search lookup and RSS parsing live against a
real show before committing to the approach, rather than assuming it
would work. 17 new tests across `common`/`podcast-manager`/
`sync-orchestrator`. Caught and fixed a real test-suite regression along
the way: the new `resolve_feed_url`/`fetch_rss_episodes` calls weren't
mocked in several existing `sync_podcast` tests, silently turning a
sub-second test run into ~100s of real network calls — fixed by mocking
`resolve_feed_url` in the shared `patched_pipeline` fixture (and the
few tests that don't use it).

## One-off RSS metadata backfill for pre-existing episodes — shipped

Follow-up to the RSS metadata feature above: user found only 2 episodes
had a populated `published_at` after the feature shipped. Root cause,
confirmed live: `record_episode()` only (re)writes metadata for a
show's *current* top-N candidates each sync run — an already-played/
archived episode still sitting locally never re-enters that loop, so it
never gets touched again once it ages out. New
`podcast_manager.download.backfill_episode_metadata()` walks every
locally-known episode instead of just current candidates, resolving/
fetching a show's feed only if it actually has episodes still needing
backfill (all four fields blank) — skips the network entirely for
already-enriched shows. New `StateDB.update_episode_metadata()`
deliberately only touches the four RSS columns, never played state —
`record_episode()`'s own upsert would risk clobbering play state for an
episode no longer an active candidate, which is exactly what this exists
to avoid. Exposed as a new standalone `podcast-manager backfill-metadata`
CLI command (same shape as `push-play-status`) — a one-off operation,
not something a normal sync should run every time.

9 new tests. Live-verified against the real account: 75 episodes
backfilled (up from 12 already covered by the main feature). The two
shows left at 0 (Dual Boot Diaries, Hard Drive) are the ones already
unsubscribed+pruned earlier this session — correctly excluded, not a
bug. The ~10 unmatched episodes are mostly older Louis Theroux ones,
likely just outside their feed's own returned item window — an inherent
RSS-source limitation, not something to fix further.

## Backup retention trim (2026-08-21): 313GB -> 134GB

`state/device_backups/` had grown to 335.7GB actual (313GB as last
eyeballed), almost entirely in the shared content-addressed `blobs/`
store. Ran `resolve_retention_map()` + `prune_and_gc_backups()`
(`services/common/src/common/backups.py`) in `dry_run=True` first to
check the impact of decisions before deleting anything for real.

**Finding**: the currently-configured global policy (`keep_last=3`,
`max_age_days=14`) only freed 102GB (-> 233GB remaining) — nowhere near
a ~100GB target. Even collapsing to "keep just the single newest
snapshot per device_id, any age" still only reached 233GB. Root cause:
this session's album-art resize (500x500) rewrote embedded tags on
nearly every primary-library audio file, so the pre-resize and
post-resize backup snapshots share almost no blob hashes with each
other — the size isn't old cruft, each device's latest full-library
snapshot is genuinely that large post-resize.

Also found `state/device_backups/` had **4 device_id dirs for only 2
physical devices**: `000A270015AE6188` and `8K6382K4V9S` are stale
historical id-aliases (an old FireWire-GUID reading for john-copy, and
john.yaml's own commented-out old serial for the primary device,
respectively) — both fully superseded by the current-serial snapshot
history under `8K13762U9ZS` (primary) and `8M74299RYMV` (john-copy).

Presented three options (dry-run numbers for each) to the user; chose
"keep both current devices' latest snapshot, drop the two stale
aliases entirely" (144GB estimated). Applied for real via
`prune_and_gc_backups(..., dry_run=False)`:
- Deleted all 10 snapshots each from `000A270015AE6188` and
  `8K6382K4V9S` (fully retired, 0 remaining).
- Kept the 1 existing snapshot each for `8K13762U9ZS` (primary's
  brand-new pre-reformat safety snapshot from 2026-08-20, untouched)
  and `8M74299RYMV` (john-copy, from 2026-08-19).
- 8,313 orphaned blobs removed, 192.0GB freed.
- Actual `du -sh` result: **134GB** (vs. the 143.7GB dry-run estimate —
  difference is filesystem block-size overhead on the byte-sum count).

Both real devices retain one current, restorable backup. The two
retired id-aliases' backup history is gone for good — acceptable since
their content was superseded, not unique.

## Schema gap (2026-08-21): PocketCastsConfig requires credentials_file even when podcasts are fully disabled

Hit while setting up `nienie.yaml` (a profile with no Pocket Casts
account of its own — `podcasts.shows: []`). Commenting out
`credentials_file` under `podcasts.pocketcasts` leaves that key as YAML
`null`, which fails `ProfileConfig` validation outright
(`podcasts.pocketcasts — Input should be a valid dictionary or instance
of ProfilePocketCastsConfig`) — even though `shows: []` means the
credentials are never actually read. `john-copy.yaml` worked around this
the same way (reusing john's own credentials_file as an unused
placeholder); did the same for `nienie.yaml`.

**To fix properly**: make `credentials_file` optional on
`ProfilePocketCastsConfig` (or make the whole `podcasts:` block
optional) when `shows: []`/no podcasts are configured, so a profile
with no Pocket Casts account doesn't need a borrowed placeholder
credentials file at all. Not fixed yet — noted here as a backlog item.

## Fixed (2026-08-21): real per-user profiles were leaking into git

`.gitignore` only listed `config/profiles/john.yaml` by literal name,
despite its own comment stating the intent was for *all* real per-user
profiles (personal playlist IDs, device serials) to stay untracked —
only `alice.yaml`/`bob.yaml` are meant as committed templates.
`john-copy.yaml`, `testbed.yaml`, and (freshly, this session)
`nienie.yaml` had all been silently committed as a result. None of this
had reached `origin/main` yet (12 commits ahead, unpushed) when found,
so nothing actually leaked to GitHub.

Fixed by switching `.gitignore` to a pattern
(`config/profiles/*.yaml` + explicit `!alice.yaml`/`!bob.yaml`
allowlist) instead of a single filename, then `git rm --cached` on the
three leaked files (kept on disk, only untracked). Going forward any
newly created real profile is covered automatically, no per-file
gitignore edit needed.

## Fixed (2026-08-21): Apple Music fetch failing with "Error fetching Apple Music homepage" — broken IPv6, not cookie expiry

Every `apple_music` fetch was failing outright (`could not fetch
playlist metadata from Apple Music: Error fetching Apple Music
homepage`), first hit while running `nienie`'s first-ever fetch.
Initially suspected the documented gamdl-cookie-expiry risk (open risk
#3), but ruled that out: `get_token()` (gamdl's own internal call,
`gamdl/api/apple_music.py`) is an unauthenticated homepage scrape,
cookies aren't even involved in that request.

**Actual root cause**: this host's IPv6 address is a non-routable ULA
(`fd00::/8`, no real uplink), but `music.apple.com` also resolves to a
real global IPv6 address. httpx/httpcore's async backend (used
internally by gamdl, with no timeout override available to us) tries
the IPv6 route first, hangs against the dead route, and hits httpx's
hardcoded ~5s default `ConnectTimeout` before ever falling back to
IPv4 — confirmed by direct reproduction: the exact same call succeeds
instantly with an explicit `timeout=15`, and fails in exactly ~5.18s
with no override. `curl` succeeds instantly on the same host because it
implements real Happy Eyeballs; httpx/anyio's async backend doesn't
fall back fast enough here.

**Fix**: `services/fetcher-apple/src/fetcher_apple/_net.py` adds
`force_ipv4_dns()` (monkeypatches `socket.getaddrinfo` to AF_INET-only)
— called at the top of `api.py`'s `list_playlists`/`get_playlist_tracks`
for the in-process metadata calls. The actual `gamdl` CLI subprocess
(`download.py`'s `_run_gamdl`/`_run_gamdl_single_track`) is a *separate*
Python process, unreachable by an in-process patch, so it gets the same
fix via a `PYTHONPATH`-injected `sitecustomize.py`
(`_sitecustomize_ipv4/`) that Python auto-imports at interpreter
startup — `_gamdl_subprocess_env()` prepends that dir to the
subprocess's own `PYTHONPATH`. Confirmed working end-to-end: `nienie`'s
full fetch + device sync completed successfully after the fix (1886
tracks added, 57m39s).

If this host's IPv6 routing ever gets fixed for real (or this project
moves to a host with working IPv6), this patch is still harmless
(IPv4-only is always a valid, if slightly less optimal, choice) but
could be removed.

## Confirmed live (2026-08-21): library_root/music is a shared library pool, not per-profile-scoped -- by design, not a bug

The `nienie.yaml` first sync (6 playlists configured) wrote **1886
tracks** to the device — not a 6-playlist-sized library. Corrected
characterization (2026-08-21): this is NOT a defect. `library_root/
music` is one shared pool across every profile, same as real iTunes'
"entire library" device sync — playlists are curation *views* into that
shared pool, not a content filter that shrinks what lands in the
device's Music library. `nienie`'s 6 playlists are correctly scoped as
actual on-device *playlists* (via `library_root/playlists/nienie/`);
the device's full Music/Songs browse view legitimately getting the same
~1886-track shared pool as every other device is expected behavior, not
something to fix. (Earlier note in this file from earlier in the
session referred to this as an "architecture gap" — that framing was
wrong; leaving this correction here rather than editing that entry
retroactively.)

## Primary device DB-wipe test result (2026-08-22): still no album art at full scale

Ran the "fast device reset" fix (delete `iPod_Control/Artwork/` +
`iPod_Control/iTunes/iTunesDB`, keep `SysInfo`, write a fresh empty
`iTunesDB`, full resync) on the real primary 7th Gen device — the same
fix that produced the BREAKTHROUGH success on the disposable testbed
unit at small scale (48 tracks/11MB). This time at full scale: **6,388
tracks, all of john's real library** (two sync passes: first wrote
6,378 tracks then failed on one straggler audiobook track due to the
host's own `/tmp` filling up — unrelated to iopenpod, see below — a
follow-up sync added the missing track cleanly, ending at 6,388/6,388,
confirmed via `iTunesDB` field `has_artwork: 1` on the previously-
missing track).

**Result: no album art rendered**, per direct user confirmation on the
device.

This is a meaningful negative result, not a wasted test:
- Doubly rules out the "orphaned/stale DB data" hypothesis — a
  genuinely fresh, from-scratch `iTunesDB`/`ArtworkDB` (no accumulated
  history at all) still fails once total size is large enough, now
  confirmed on a second, real device (not just the testbed unit).
- Consistent with (does not contradict) the binary-search finding on
  the testbed device: a real total `ArtworkDB`+`.ithmb` size ceiling
  somewhere in [327MB, 340MB] for that unit. The primary device's full
  library at 6,388 tracks is almost certainly far beyond any similarly-
  scaled ceiling for this device/generation — real library dedup ratio
  is better than the testbed's random-sample library (~3:1 vs ~1.1:1,
  established earlier this session), but 6,388 tracks' worth of unique
  album art still vastly exceeds a few hundred MB.
- Does NOT yet establish the primary (7th Gen) device's own specific
  ceiling — only the testbed (6th Gen/80GB) unit's ~327-340MB range is
  measured. The two devices could plausibly have different thresholds
  (different generation/model/firmware), not yet tested independently
  on primary.

**Unrelated finding from this same test run**: the DB-wipe test's first
pass failed on one track ("Animal Farm" audiobook) with `[Errno 28] No
space left on device` — traced to the HOST's own `/tmp` tmpfs being
95%+ full, not an iopenpod/device issue. Root cause: this session's own
`/tmp/claude-*/.../scratchpad/testbed_library_1530/` (13GB, a fully-
documented, already-superseded binary-search test library from earlier
today) had never been cleaned up. Deleted it, freed `/tmp` back to ~1%
used, re-ran and picked up the missing track cleanly. Not a code bug —
environmental/session hygiene only — but worth remembering: `/tmp`
scratchpad growth during heavy testbed work can cause real, confusing
mid-sync failures on unrelated real-device syncs sharing the same host.

## Investigation paused (2026-08-22)

User decision after the primary-device DB-wipe result above: pause the
album-art size-ceiling investigation here for now. Options considered
and set aside (not ruled out, just not pursued today): binary-searching
the primary device's own specific ceiling directly (expensive — each
full-scale cycle took 2-3 hours today, realistically a multi-day
effort), or provisionally treating the testbed's ~327-340MB range as a
working proxy and keeping primary's active scope under it. Real iTunes
byte-diff comparison (Mac/Windows) remains the one untried lever if
this gets picked back up later.

## 2026-08-23: Rockbox mode added — sidesteps the album-art ceiling for good, doesn't fix it

Earlier in this file's own binary-search writeup (album art size-ceiling
section, "F1055 128x128... matching real iTunes' own set exactly"),
Rockbox was noted as "considered as a hypothetical alternative firmware
mid-session and set aside as out of scope — its album art mechanism is
architecturally unrelated to Apple's ArtworkDB anyway, so this bug
wouldn't apply there regardless." That's now built: `sync.mode: rockbox`
on a profile (default `itunes`, every existing profile unaffected)
routes device sync through a new `services/sync-orchestrator/src/
sync_orchestrator/rockbox_sync.py` instead of `sync.py` — a plain
filesystem mirror, no `iTunesDB`/`ArtworkDB`/`SyncEngine` at all. Full
design writeup and reasoning: `services/sync-orchestrator/README.md`'s
"Rockbox mode" section.

Two things worth recording here specifically, since both were found by
reading code rather than being obvious from the task itself:

- iopenpod already ships an `EngineOptions.rockbox_metadata_support`
  option (`sync/rockbox_metadata.py`) — looked like a possible shortcut
  at first, but it's a *bolt-on* pass on top of the normal iTunesDB
  sync (dual-boot use case): iTunesDB/ArtworkDB still get written in
  full (still hits the size ceiling above), and its tag writer is
  coupled to the obfuscated `iPod_Control/Music/...` layout and
  `TrackInfo` objects from that same pipeline. Bigger problem: iopenpod
  parses `.m3u8` files found in a synced folder purely as iTunesDB
  playlist *definitions* (`sync/sync_playlist_files.py`) and never
  copies the physical file to the device — under that option, Rockbox
  (which never reads iTunesDB) would see no playlists at all. Not used
  for this reason.
- ffmpeg's audio-only transcode commands (`_cmd_alac`/`_cmd_aac`/
  `_cmd_mp3` in iopenpod's `transcoder.py`) all pass `-vn`, which strips
  any embedded cover-art stream even though basic text tags survive by
  ffmpeg's own default metadata handling. So a straight file copy (no
  transcode needed) preserves art for free, but any track that actually
  gets transcoded loses it unless re-embedded afterward — `rockbox_sync.
  execute_rockbox_sync` does this by reading the original art straight
  from the source file (mutagen) and re-embedding it into the transcoded
  output. Confirmed by reading the ffmpeg command-builder source, not
  yet confirmed live against a real Rockbox device.

Genuinely open, unverified against a real device: whether a Rockbox-
loaded iPod still exposes `iPod_Control/Device/SysInfo` (device.py's
`is_ipod_mount` now also accepts a `.rockbox/` directory, belt-and-
suspenders, but this hasn't been tested live either way).

## 2026-08-24: profile.music — opt-in scoping of the shared library pool, per-device

User ran a real sync on nienie's iPod (stock firmware, iTunes mode —
`sync.mode` was never set on her profile) and found far more tracks on
the device than expected, most not on any of her configured playlists.
This is the shared-library-pool behavior documented earlier in this file
and in [[feedback-shared-library-pool-not-a-bug]] — confirmed still
correct as the *default* (John's profile genuinely wants everything),
but the memory's own "how to apply" note had already named the fix for
a profile that wants something narrower: an isolated library scope, the
same technique disposable testbed devices already used. That's now a
real, opt-in profile field (`ProfileConfig.music`, `MusicLibraryConfig`
in `common/models.py`) instead of a testbed-only trick — see
`services/sync-orchestrator/README.md`'s "Music library scope" section
for the full design and the exact reasoning behind its empty-selections
default (opposite convention from `audiobooks:`, matching
`external_library:` instead).

The one thing worth recording here specifically: a profile's own
playlist tracks are **always** included regardless of this scoping, and
that turned out to require zero extra iTunes-mode work — iopenpod's own
playlist-file discovery (`sync/sync_playlist_files.py`,
`playlist_parser.py`'s `PlaylistPathResolver`) resolves each `.m3u8`
entry's track by a direct filesystem existence check on its absolute
path, completely unconstrained by `pc_folders`. So dropping
`library/music` from `pc_folders` for a scoped profile never drops a
playlist's own tracks in iTunes mode — confirmed by reading iopenpod's
source, not by guessing. Rockbox mode (this project's own code, not
iopenpod's) doesn't get that guarantee for free — `rockbox_sync.py` had
to be taught to add a playlist's tracks into its own `desired` set
explicitly whenever `profile.music` scoping had excluded them, and to
defer its add/remove/update diff until after that happens (moved
`_diff_plan` to run after the playlist loop, not before — an ordering
bug that would have silently excluded playlist tracks from Rockbox mode
devices otherwise).

`nienie.yaml` (real, gitignored profile) updated to
`music: {mode: include, selections: []}` — exactly her playlists' own
tracks, nothing else — as the actual fix for the device she just
reported this on.

## 2026-08-24: stale playlist never removed from device after dropping it from a profile

User tested the `music.` scoping fix above live on nienie's device
(worked) but found one leftover: a playlist removed from `nienie.yaml`'s
`playlists:` list was still on the device after a
`sync-orchestrator sync --execute --allow-removals` run.

Root cause, confirmed by reading the fetch pipeline: nothing anywhere
ever deletes a playlist's `.m3u8` file once it's dropped from a
profile's config. Each fetcher's `download.py` only ever calls
`common.playlist.write_m3u8()` for playlists it's currently told to
sync — there was no prune step for ones no longer listed. And
`sync-orchestrator`'s device sync (`plan_sync`) can't fix this on its
own either: it only ever scans whatever `.m3u8` files physically exist
under `library/playlists/{profile}/` as pc_folders — from its point of
view a leftover stale file is just as "real" and "wanted" as any other
playlist, so it kept re-syncing it forever. The user had also only run
`sync-orchestrator sync` directly (the device-write step), never
`music-stack sync` (the fetch step that owns that folder) — so even a
fix couldn't have taken effect from the command they ran.

Fixed at the actual point of ownership: `common.playlist.
prune_removed_playlists()` (new) deletes any `.m3u8` under
`playlists_root/{profile}` whose stem isn't among the profile's current
playlist names, called from `music_stack_cli.orchestrate.run_sync()`
whenever a music source is active this run — same pattern
`podcast_manager.download.prune_unsubscribed_shows` already established
for podcast shows, including the same "must run against the full,
unfiltered list, never a `--playlist`-narrowed subset" rule (narrowing
is a per-run scope choice, not an "I removed this" signal).

**Operationally**: removing a playlist from a profile now requires an
actual fetch (`music-stack sync`, or `sync-orchestrator full-sync`,
which does both stages) — running `sync-orchestrator sync` alone will
never prune a stale playlist, by design (it doesn't own that folder).

## 2026-08-24: album-art investigation resumed — confirmed it's a byte-size ceiling, not a bad track; and why source art compression can't fix it

Investigation un-paused at the user's request, using the disposable 6th
Gen testbed device (still sitting untouched at its last state from the
2026-08-20 growth-rate test: 1,715 tracks/364MB, no art — see "growth
*rate* vs. total size" above). The user's hypothesis going in: a single
bad track (or a handful) causes the failure, not scale itself, since
they'd personally seen other real iPods with 5,000+ tracks and visible
art.

**Methodology note, a real mistake caught mid-session**: the device's
own iTunesDB cleanly splits its 1,715 tracks into two batches by
`date_added` — 1,555 tracks from the last known-*working* sync
(19:48 UTC) and 160 tracks from the failed jump 22 minutes later
(20:10 UTC), giving an exact reproduction of the original growth-rate
test's two checkpoints without needing the long-deleted source library.
First attempt extracted both batches' audio files straight off the
device's own `iPod_Control/Music/` — but a real iPod stores **no
metadata or art in the file itself**; title/artist/album/art all live
in the separate iTunesDB/ArtworkDB. Syncing those extracted files
produced "Unknown Artist/Unknown Album" and (correctly, given the
input) no art at all — a red herring, not a real result. Fixed by
matching each track (title+artist, normalized) back against the real,
still-intact source files in `library/music/` (which do carry embedded
tags/art) and building the test library from those instead — a
one-off track-number-stripping regex bug in the matcher briefly ate
real digits out of titles like "4 Minute Warning"/"24/7" before all
1,715 tracks matched cleanly.

**Recreated baseline**: 1,534 tracks, 338MB (below, after settling) —
confirmed **album art renders correctly**, matching the original
"1,550 works" checkpoint almost exactly. Bisected the 160 candidate
tracks from the failed jump by halving (160→80→40→20→10→5→2), each
round confirming album art broken at every step down to a specific
pair: **The Big Moon "Fun" + Slackr "Friends with you"** (baseline +
these 2 = 1,536 tracks) reproduced the failure.

**Then the actual finding, once isolating further**: neither track
fails on its own (baseline + Big Moon alone = 1,535, works; baseline−2
+ Slackr alone = 1,533, works), and the *pair* together still works
when the total count is pushed below 1,536 (baseline−1 + both = 1,533,
works). But the exact same pair at 1,536 fails. **Decisive test**:
baseline + two different, previously-untested tracks (Rosa Walton &
Hallie Coggins, The Beatles "Hey Jude") landing at the *identical*
1,536 track count — **works, art renders**. Same count, different
content, opposite results. This rules out both "one bad track" and
pure track-count as the driving variable. Measured the `Artwork`
directory at this working 1,536-track state: **353,663,066 bytes
(338MB)** — squarely inside the original binary search's [326MB,
340MB] range from 2026-08-20, independently reproducing and tightening
confidence in that same byte-size-ceiling finding via a completely
different content set. Big Moon + Slackr's combined art evidently
pushed the same 1,536-track state just over the real boundary; Rosa
Walton + Hey Jude's didn't. Round 12 (this exact test) hit a real
mid-write device disconnect on the first attempt — iopenpod's own
`filesystem_safety` check aborted cleanly before writing
(`mount instance changed after inspection`), iTunesDB was verified
still consistent at its pre-sync 1,535-track state afterward, no
corruption; the retry completed cleanly. The testbed device's USB
connection dropped and needed remounting close to a dozen times over
this session — environmental, not a code issue, but worth flagging if
this device is used again.

**User's follow-up question, with an important structural answer**: can
shrinking source album art file sizes reduce the on-device footprint
for a much larger (60k-track) library? **No — confirmed by reading
iopenpod's own encoder** (`artworkdb_writer/ithmb_codecs.py`/
`rgb565.py`): on-device art is never a compressed image file at all.
Every embedded cover gets decoded and re-encoded to exactly three
**fixed-size, uncompressed RGB565 raw pixel** formats, matching real
iTunes' own shape (see the "F1055/F1060/F1061... matching real iTunes'
own set exactly" note earlier in this file):

| Format | Resolution | Size (fixed, always) |
|---|---|---|
| F1055 | 128×128 | 32,768 bytes |
| F1060 | 320×320 | 204,800 bytes |
| F1061 | 55×55 | 6,050 bytes |
| **Total per unique image** | | **~243KB, always** |

A source cover that's 20KB or 5MB costs the device the exact same
~243KB either way — source compression/resolution has **zero** effect
on `ArtworkDB` size. The only two real levers are dedup (already
near-optimal — one image shared per album, ~99% efficient in earlier
testing) and the raw count of unique album images synced. Back-of-
envelope from the measured 338MB/1,536-track boundary: ≈338MB ÷ 243KB
≈ **~1,400 unique album images** is roughly this device's real ceiling,
independent of track count. For 60,000 tracks to fit under that would
need a ~43:1 track-to-unique-image dedup ratio — unrealistic even for a
well-organized library (this project's own real primary library
measured ~3:1, see the dedup-efficiency note earlier in this file). So
even best-case, 60k tracks would need ~20,000 unique images ≈ 4.7GB,
nowhere close.

**Practical implication (superseded below — kept for the record)**: at
this point in the investigation the working theory was still "a real
`ArtworkDB` byte-size ceiling, ~326-340MB, tracking unique image count."
Continued testing the same session overturned that as the *primary*
explanation — see the next entry.

## 2026-08-25: root cause found — not a size ceiling, a specific bad file ("Cool Kids" by Echosmith)

Continued growing past the working 1,536-track/338MB state with fresh,
previously-untested tracks (a session/machine restart wiped the scratch
staging dir mid-session — re-matched the device's then-current 1,545
tracks back against the real library by title/artist, 100% match, no
data lost since the device's own physical state survived untouched):

| Tracks | Artwork dir | Result |
|---|---|---|
| 1,538 | — | works |
| 1,542 | — | works |
| 1,545 | 339.4MB | works |
| 1,549 | **340.5MB** | **works** — already past the old "340MB" ceiling assumption |
| 1,557 | **327.5MB** | **fails** — *fewer* bytes than the working 1,549 state |

The 1,557 result is the key one: total `ArtworkDB` bytes went *down*
between a working and a failing checkpoint (most likely `.ithmb`
chunk-packing/allocation across the fixed 32MB-per-file boundary
reshuffling non-linearly with content, not a smooth function of byte
count — not fully explained, but the byte-size-ceiling theory cannot
account for a failure at a lower byte count than an already-confirmed
success). This directly falsified pure total-size as the explanation
for at least this failure.

Bisected the 8 tracks added between 1,549 (works) and 1,557 (fails) by
halving (8→7→3→1) down to a single track: **"Cool Kids" by Echosmith**
(from the "100 Greatest Throwback Songs" compilation). Confirmed
decisively, the same rigor as the earlier Big Moon/Slackr false lead:
- Baseline alone (1,545, confirmed working) + Cool Kids alone (1,546)
  → **fails**.
- Cool Kids alone at a count *below* the confirmed-safe baseline
  (baseline −3 tracks, 1,543 total) → **still fails** — rules out a
  size-crossing coincidence this time, unlike Big Moon/Slackr.
- Same exact 1,543 count with Cool Kids swapped out for a different
  track ("Drops of Jupiter (Tell Me)") → **works**. Same count,
  different content, opposite result — this is a real per-file defect,
  not a size effect.

**What's different about this file**: its embedded JPEG carries a DRI
(Define Restart Interval) marker (`0xFFDD`) plus an APP13/Photoshop
segment — a real, inspectable marker-level difference, consistent with
the file having been processed through Photoshop or similar tooling at
some point (also explains an earlier, otherwise-unremarkable oddity:
this file carries EXIF metadata in its cover art, unusual for album
art). Confirmed the fix by isolating resize from re-encode:
- Resizing Cool Kids' art 1200×1200 → 500×500 (through Pillow) → art
  renders again.
- Re-encoding at the **same** 1200×1200 (decode + re-save through
  Pillow, no resize at all) → **also** renders again.

Since both fix it, the size reduction was never the actual variable —
Pillow's own JPEG encoder never writes restart markers, so any Pillow
re-encode strips whatever's actually mishandled downstream (not
established whether that's iopenpod's own art-extraction/RGB565
conversion path, or the device firmware's own thumbnail decoder — out
of scope to chase further given a working fix in hand).

**Why this isn't a simple "scan for the marker and warn" fix**: checked
how common each candidate signal is across the real library (1,997
tracks with embedded art) — EXIF: 1,897 tracks (95%), DRI marker: 1,887
tracks (94.5%), including several *already individually confirmed
working* earlier this same session (Slackr, RAT BOY, Koyo, The Bouncing
Souls, Chief State, Making Friends all carry the DRI marker and
rendered fine). Also checked non-square art dimensions (a separate, much
rarer trait — 44 tracks, 2.2%) as an alternate lead: batch-tested all
44 together against the confirmed-good baseline (with Cool Kids
excluded) — worked fine, ruling out non-square art as a general failure
class too. So no cheap library-wide signal was found that reliably
separates "affected" from "shares a marker, but fine" — whatever's
actually wrong with Cool Kids' specific encoding remains only
empirically characterized, not fully root-caused at the byte level.

**Fix shipped**: `services/library-manager/src/library_manager/
artwork.py` (new) re-encodes embedded cover art through Pillow,
unconditionally, for any track whose art shows one of the markers
Pillow's own encoder never writes (DRI, APP13/Photoshop, APP14/Adobe,
progressive SOF2) — given no cheap way was found to predict which files
are actually affected, and the cost is low (thumbnails ultimately
downsampled to ≤320×320 on-device; Pillow re-encode at quality 90 is
visually imperceptible at that scale). New `library-manager
normalize-artwork` CLI command + `LibraryManagerConfig.
normalize_artwork_enabled` config flag, wired into `fetch-scheduler`'s
maintenance tasks the same way dedup/cleanup already are. Built on
`fix/normalize-embedded-artwork`, not yet merged to `main` — the user's
explicit call was to verify via a real device test first and merge
based on that working, independent of having fully root-caused the
underlying mechanism. See that branch's own commits for the real-device
verification result once run.

**Revised overall status (superseded below)**: at this point the working
theory was that the original [326MB, 340MB] finding was likely just
Cool-Kids-class bad tracks masquerading as a size threshold. Real-device
verification below settled it more precisely — both explanations turned
out to be real and independent.

## 2026-08-25: real-device verification — the Cool Kids fix works, AND there's a separate, real size ceiling

Full real-device verification of the `library-manager normalize-artwork`
fix (`fix/normalize-embedded-artwork` branch), following the plan's own
bar for merging (verify on real hardware, merge on that working
regardless of whether the underlying mechanism is fully understood):

1. Ran `library-manager normalize-artwork --library-root library/music`
   for real against the live library (1,998 tracks scanned) — re-ran
   immediately after to confirm idempotency (`scanned 1998, normalized
   0`, as designed).
2. **Clean-wipe test, 1,547 tracks, 340.5MB Artwork**: full fresh sync
   (`iPod_Control/Artwork`+`Music`+`iTunesDB`+iOpenPod mapping cache all
   wiped first) including the real, now-fixed Cool Kids file — **album
   art renders**. This is the actual proof the fix works: same real
   file, same shipped code path, no leftover device-side cache or
   staleness possible.
   - Worth recording: an earlier *incremental* sync of just the fixed
     Cool Kids file onto an already-heavily-churned device (dozens of
     add/remove cycles earlier the same day, no fresh wipe) still
     failed to render, despite the file itself being verified
     byte-identical to what worked in ad hoc testing. Root cause not
     fully pinned down, but consistent with this project's own
     documented iopenpod integrity-check gotcha (`services/sync-
     orchestrator/sync.py` era: iopenpod's fingerprint/mapping cache
     not always correctly invalidating when file *content* changes at
     the same path) — a clean wipe is what actually resolved the
     ambiguity, not further inspection of the file itself.
3. **Full real library, 1,958 tracks**: synced the *entire* real
   `library/music`, both incrementally (429.8MB Artwork, still failed
   to render) and via a second full clean wipe (394.8MB Artwork,
   *still* failed to render, ruling out incremental-update staleness as
   the explanation this time). Cool Kids' fix was intact and unrelated
   to this failure — this is a **second, independent effect**.

**Conclusion**: both things found this session are real and coexist.
The Cool Kids-class bug (specific tracks with Photoshop-processed/
restart-interval JPEG art) is real, reproducible, and now fixed by
`normalize-artwork`. A **separate, real total-`ArtworkDB`-size ceiling
also exists** on this 6th Gen/80GB device — now bounded more tightly
than the original 2026-08-20 binary search: confirmed **working at
1,547 tracks/340.5MB**, confirmed **failing at 1,958 tracks/394.8MB**
(clean wipe, Cool Kids fix in place either way). The original [326MB,
340MB] figure was real after all; this session's data narrows the
upper edge of "still works" slightly higher (340.5MB) than previously
measured, consistent with normal noise/different track mix rather than
a contradiction.

**Decision**: merge the `normalize-artwork` fix to `main` now — it's a
real, verified fix for a real, distinct bug, independent of the
still-open size ceiling. The size ceiling itself remains a separate,
still-open, paused investigation (per the user's earlier 2026-08-22
decision) — `profile.music` scoping and Rockbox mode remain the actual
mitigations for a device whose desired library exceeds this device's
own real ceiling, not something `normalize-artwork` was ever meant to
solve.
