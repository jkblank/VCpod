from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from common.lock import FileLock, LockTimeoutError
from common.state import EpisodeRecord, StateDB

from podcast_manager.api import (
    FullEpisode,
    PodcastSummary,
    list_episode_states,
    list_full_episodes,
    update_episode_status,
)
from podcast_manager.rss import fetch_feed_image_url, fetch_rss_episodes, resolve_feed_url

logger = logging.getLogger(__name__)

_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize(text: str) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", text).strip()
    return cleaned or "Untitled"


def _guess_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".mp3"


_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}


def _guess_image_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in _IMAGE_EXTS else ".jpg"


def _episode_path(show_dir: Path, episode: FullEpisode) -> Path:
    # Embeds the Pocket Casts episode uuid so the path is unique per episode
    # by construction (some shows reuse generic titles) and so two profiles
    # sharing a show resolve to the exact same file.
    ext = _guess_extension(episode.url)
    return show_dir / f"{_sanitize(episode.title)} [{episode.uuid}]{ext}"


@dataclass
class SyncResult:
    downloaded: list[EpisodeRecord] = field(default_factory=list)
    already_present: list[EpisodeRecord] = field(default_factory=list)
    failed: list[tuple[FullEpisode, str]] = field(default_factory=list)
    deleted: list[EpisodeRecord] = field(default_factory=list)


# Episode audio files are commonly tens of MB — httpx's default 5s timeout
# is nowhere near enough (confirmed live: a real ~30MB episode timed out).
_DOWNLOAD_TIMEOUT = httpx.Timeout(10.0, read=120.0)

# Confirmed live (2026-07-19): 6 episode downloads failed across 3 unrelated
# CDN hosts (megaphone.fm, podtrac.com, podbean.com) in one sync run, all
# ReadTimeout/RemoteProtocolError partway through — every single one a
# 30-90 minute episode, the longest in its show. Not a host-specific or
# code bug, just transient drops that are simply more likely to hit a long
# streaming download somewhere along the way. A few retries with backoff
# clears most of these without any user intervention.
_DOWNLOAD_RETRIES = 3
_DOWNLOAD_RETRY_BACKOFF_SECONDS = 5.0


def _download_enclosure(url: str, dest: Path) -> None:
    # Download to a temp path and only rename into place on full success —
    # confirmed live that a mid-download failure (e.g. a timeout) otherwise
    # leaves a truncated file at `dest`, which a later run's `dest.exists()`
    # check would then wrongly treat as a completed download.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_dest = dest.with_suffix(dest.suffix + ".part")
    last_error: httpx.HTTPError | None = None
    try:
        for attempt in range(1, _DOWNLOAD_RETRIES + 1):
            try:
                with httpx.stream(
                    "GET", url, follow_redirects=True, timeout=_DOWNLOAD_TIMEOUT
                ) as resp:
                    resp.raise_for_status()
                    with tmp_dest.open("wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
                tmp_dest.rename(dest)
                return
            except httpx.HTTPError as exc:
                last_error = exc
                tmp_dest.unlink(missing_ok=True)
                if attempt < _DOWNLOAD_RETRIES:
                    time.sleep(_DOWNLOAD_RETRY_BACKOFF_SECONDS * attempt)
        assert last_error is not None
        raise last_error
    finally:
        tmp_dest.unlink(missing_ok=True)


def sync_podcast(
    *,
    podcast: PodcastSummary,
    token: str,
    library_root: Path | str,
    state_db_path: Path | str,
    sync_unplayed_only: bool = True,
    max_episodes_per_show: int = 5,
    fill_mode: str = "newest",
    episode_filter: str = "played",
    delete_played_episodes: bool = True,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> SyncResult:
    # Must be resolved to absolute: a relative library_root here produces
    # a relative local_path recorded in the state db, which
    # sync-orchestrator's _load_podcast_feeds() can't reliably re-resolve
    # later (it joins onto its own library_root, silently producing a
    # wrong doubled path if the stored value is relative to something
    # else, e.g. the CWD a much earlier invocation happened to have) —
    # confirmed live: 11 of 12 subscribed shows' episodes were silently
    # missing from every real device sync as a result, despite being
    # fully downloaded and recorded, because their (relative) local_path
    # values never resolved correctly downstream. Same bug class as
    # fetcher-apple/fetcher-ytmusic's library_root handling — see
    # CLAUDE.md and notes.md.
    library_root = Path(library_root).resolve()
    if lock_path is None:
        lock_path = Path(state_db_path).parent / ".podcasts.lock"
    result = SyncResult()

    full_episodes = list_full_episodes(token, podcast.uuid)
    states_by_uuid = {s.uuid: s for s in list_episode_states(token, podcast.uuid)}

    # Best-effort RSS enrichment (description/episode/season number —
    # Pocket Casts' own API doesn't expose any of these, confirmed live
    # against /podcast/full/, see notes.md). Resolved once per show per
    # run, not per episode. A show whose feed can't be resolved/parsed
    # just gets no enrichment this run (resolve_feed_url/fetch_rss_episodes
    # already degrade to None/[] rather than raising) — every episode
    # still downloads normally either way. Keyed by enclosure URL, which
    # matches Pocket Casts' own FullEpisode.url byte-for-byte (confirmed
    # live against a real feed).
    rss_by_enclosure_url = {}
    feed_url = resolve_feed_url(podcast.title, podcast.author)
    if feed_url:
        rss_by_enclosure_url = {ep.enclosure_url: ep for ep in fetch_rss_episodes(feed_url)}

    # "newest" (default): always grab the latest unheard episode(s) —
    # right for news/commentary shows. "next": grab the oldest unheard
    # episode(s) first instead, resuming chronologically — right for
    # serialized fiction, courses, anything where episode order matters.
    # See notes.md.
    candidates = sorted(
        full_episodes, key=lambda e: e.published or "", reverse=(fill_mode != "next")
    )

    show_dir = library_root / _sanitize(podcast.title)

    # Episode audio files carry no embedded art of their own, and never
    # will (podcast RSS enclosures essentially never embed per-episode
    # art) -- but iopenpod's own art extraction already falls back to a
    # folder-level cover image sitting next to the audio file if there's
    # no embedded art, and every episode of a show lands in this same
    # show_dir. So a single show-level cover, written once, retroactively
    # gives every episode on-device artwork for free with no changes
    # needed on the sync-orchestrator/iopenpod side. Skipped once a cover
    # already exists (whatever extension it landed as) -- no need to
    # re-fetch the feed just for this on every run. Best-effort, same as
    # every other RSS-sourced enrichment in this module: a show with no
    # resolvable feed or no <image> tag just goes without a cover.
    if feed_url and not any(show_dir.glob("cover.*")):
        image_url = fetch_feed_image_url(feed_url)
        if image_url:
            try:
                _download_enclosure(image_url, show_dir / f"cover{_guess_image_extension(image_url)}")
            except httpx.HTTPError as exc:
                logger.warning("podcasts: could not download cover art for %r: %s", podcast.title, exc)

    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        # Pocket Casts' own EpisodeState only has a row for episodes the
        # user interacted with through a Pocket Casts client — a real
        # listen elsewhere (or sync lag) can leave no row at all, wrongly
        # treating a played episode as unplayed (see notes.md). Local
        # state.sqlite can already know better: sync-orchestrator's M8
        # device read-back (playstate.py) records played state straight
        # from the iPod's own Play Counts file, independent of Pocket
        # Casts ever seeing it. Treat "played" as true if *either* source
        # says so — never let a locally-confirmed play get re-treated as
        # unplayed just because Pocket Casts hasn't caught up.
        local_by_uuid = {e.episode_uuid: e for e in db.list_episodes()}

        def _merged_played_state(episode_uuid: str) -> tuple[bool, int]:
            state = states_by_uuid.get(episode_uuid)
            local = local_by_uuid.get(episode_uuid)
            remote_played = bool(state and state.played)
            remote_played_up_to = state.played_up_to if state else 0
            local_played = bool(local and local.played)
            local_played_up_to = local.played_up_to if local else 0
            return remote_played or local_played, max(remote_played_up_to, local_played_up_to)

        def _is_played(episode_uuid: str) -> bool:
            return _merged_played_state(episode_uuid)[0]

        def _is_archived(episode_uuid: str) -> bool:
            # Archive is purely a Pocket Casts app/user action — unlike
            # played state, there's no device-side equivalent to merge in
            # (sync-orchestrator's M8 read-back only ever knows play
            # counts/position, not archive status), so this only ever
            # checks the remote state.
            remote = states_by_uuid.get(episode_uuid)
            return bool(remote and remote.archived)

        _is_done = _is_archived if episode_filter == "archived" else _is_played

        # Refresh remote-confirmed play state for every already-downloaded
        # episode of this show, not just this run's download candidates
        # below. Without this, an episode played only through the Pocket
        # Casts app (never round-tripped through the device) goes stale
        # forever the moment sync_unplayed_only excludes it from
        # candidates — its state-db row would keep reporting played=False
        # even though Pocket Casts already knows better, which would also
        # silently defeat delete_played_episodes below.
        for episode in full_episodes:
            if episode.uuid not in local_by_uuid:
                continue  # never downloaded — nothing to refresh
            played, played_up_to = _merged_played_state(episode.uuid)
            db.record_remote_play_state(episode.uuid, played=played, played_up_to=played_up_to)

        if sync_unplayed_only:
            candidates = [e for e in candidates if not _is_done(e.uuid)]

        candidates = candidates[:max_episodes_per_show]

        for episode in candidates:
            dest = _episode_path(show_dir, episode)
            already_downloaded = dest.exists()
            if not already_downloaded:
                # One episode's connection dropping mid-download (confirmed
                # live: a large ~127MB episode repeatedly hit
                # RemoteProtocolError/ReadTimeout partway through) must not
                # abort the rest of this show's — or the whole profile's —
                # sync. _download_enclosure already cleans up its .part
                # file on failure, so this episode is simply retried
                # (from scratch) on the next sync run.
                try:
                    _download_enclosure(episode.url, dest)
                except httpx.HTTPError as exc:
                    result.failed.append((episode, str(exc)))
                    continue

            # title/audio_url/duration_seconds come from the fresh
            # list_full_episodes() call above, so every sync_podcast() run
            # backfills them regardless of already_downloaded — no need to
            # redownload a file just because an older record predates these
            # fields (and doing so would break cross-profile file sharing:
            # a second profile syncing the same episode has no local record
            # for it yet, but the file may already exist from another
            # profile's download).
            #
            # played/played_up_to are merged (OR'd / max'd), not simply
            # overwritten from Pocket Casts — otherwise this same call
            # would silently undo an M8 device read-back's played=True the
            # moment Pocket Casts' own state hasn't (yet, or ever) caught
            # up. record_episode's own ON CONFLICT already leaves
            # pending_push untouched; this closes the equivalent gap for
            # played/played_up_to.
            played, played_up_to = _merged_played_state(episode.uuid)
            rss_meta = rss_by_enclosure_url.get(episode.url)
            record = EpisodeRecord(
                episode_uuid=episode.uuid,
                podcast_uuid=podcast.uuid,
                show_name=podcast.title,
                local_path=str(dest),
                played=played,
                played_up_to=played_up_to,
                downloaded_at=datetime.now(timezone.utc).isoformat(),
                title=episode.title,
                audio_url=episode.url,
                duration_seconds=episode.duration,
                description=rss_meta.description if rss_meta else "",
                episode_number=rss_meta.episode_number if rss_meta else None,
                season_number=rss_meta.season_number if rss_meta else None,
                # Pocket Casts' own published date is a reliable fallback
                # when RSS enrichment missed this specific episode (feed
                # unresolved, or the item aged out of the feed already) —
                # both are ISO-ish timestamps for the same real value.
                published_at=(rss_meta.published if rss_meta else None) or episode.published or "",
            )
            db.record_episode(record)
            (result.already_present if already_downloaded else result.downloaded).append(record)

        # Only takes effect alongside sync_unplayed_only — see
        # delete_played_episodes' docstring in common/models.py for why
        # sync_unplayed_only=False (deliberately keeping played episodes
        # downloaded, e.g. as an archive) must not be undermined by
        # deleting them the instant they land.
        if delete_played_episodes and sync_unplayed_only:
            candidate_uuids = {episode.uuid for episode in candidates}
            full_episode_uuids = {episode.uuid for episode in full_episodes}
            for local in local_by_uuid.values():
                if local.podcast_uuid != podcast.uuid:
                    continue
                played, _played_up_to = _merged_played_state(local.episode_uuid)
                # Prune on either signal: played (original behavior), or
                # simply no longer in this run's top-max_episodes_per_show
                # window (confirmed live, 2026-08-18: candidates[:N] only
                # ever capped *additions* — nothing pruned the accumulated
                # backlog, so several shows built up well past their
                # configured limit, e.g. 17 local episodes against a
                # limit of 5. This closes that gap: an episode that ages
                # out of the window because newer ones arrived is removed
                # immediately instead of sitting there forever). Only
                # candidates still eligible this run (i.e. present in
                # `full_episodes`) count as "aged out" -- an episode
                # already excluded from `candidates` because it's done
                # is handled by the played branch above, not this one.
                aged_out_of_window = (
                    local.episode_uuid not in candidate_uuids
                    and local.episode_uuid in full_episode_uuids
                    and not _is_done(local.episode_uuid)
                )
                if not played and not aged_out_of_window:
                    continue
                path = Path(local.local_path)
                if path.is_file():
                    path.unlink()
                    result.deleted.append(local)

    return result


@dataclass
class ShowSyncOutcome:
    podcast: PodcastSummary
    result: SyncResult | None
    error: str | None


def sync_shows(
    subscriptions: list[PodcastSummary],
    *,
    token: str,
    library_root: Path | str,
    state_db_path: Path | str,
    sync_unplayed_only: bool = True,
    max_episodes_per_show: int = 5,
    fill_modes: dict[str, str] | None = None,
    episode_filter: str = "played",
    delete_played_episodes: bool = True,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> list[ShowSyncOutcome]:
    """Sync each show in turn, same as calling sync_podcast() once per show,
    except one show's failure doesn't stop the rest — e.g. a per-show API
    call (list_full_episodes) timing out, which happens before sync_podcast's
    own per-episode error handling ever gets a chance to run."""
    fill_modes = fill_modes or {}
    outcomes: list[ShowSyncOutcome] = []
    for podcast in subscriptions:
        try:
            result = sync_podcast(
                podcast=podcast,
                token=token,
                library_root=library_root,
                state_db_path=state_db_path,
                sync_unplayed_only=sync_unplayed_only,
                max_episodes_per_show=max_episodes_per_show,
                fill_mode=fill_modes.get(podcast.uuid, "newest"),
                episode_filter=episode_filter,
                delete_played_episodes=delete_played_episodes,
                lock_path=lock_path,
                lock_timeout=lock_timeout,
            )
        except (LockTimeoutError, httpx.HTTPError, OSError) as e:
            outcomes.append(ShowSyncOutcome(podcast=podcast, result=None, error=str(e)))
            continue
        outcomes.append(ShowSyncOutcome(podcast=podcast, result=result, error=None))
    return outcomes


def _other_profile_still_wants_show(
    podcast_uuid: str, this_state_db_path: Path
) -> bool:
    """Podcast audio files are shared/deduped across profiles (no profile
    name in the path -- see _episode_path/CLAUDE.md), but play/subscribe
    state is tracked per-profile in each profile's own state db. Before
    physically deleting a show's file because *this* profile unsubscribed,
    check every sibling state db in the same directory: if any of them
    still has a non-unsubscribed row for this podcast_uuid, another
    profile is still using the file and it must not be deleted."""
    for sibling in this_state_db_path.parent.glob("*.sqlite"):
        if sibling == this_state_db_path:
            continue
        with StateDB(sibling) as db:
            for episode in db.list_episodes():
                if episode.podcast_uuid == podcast_uuid and not episode.unsubscribed:
                    return True
    return False


def prune_unsubscribed_shows(
    all_subscriptions: list[PodcastSummary],
    *,
    state_db_path: Path | str,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> list[EpisodeRecord]:
    """Deletes local files and flags EpisodeRecord.unsubscribed=True for
    every downloaded episode whose show is no longer among the account's
    current Pocket Casts subscriptions.

    `all_subscriptions` MUST be the full, unfiltered account subscription
    list -- never a --show-narrowed subset. A sync run scoped to specific
    shows is choosing not to touch the rest this time, not reporting that
    the user unsubscribed from them; narrowing here would wrongly prune
    every show outside that run's scope.

    Device-side removal is a separate step (sync-orchestrator's
    build_podcast_removal_items, on its next run) -- this only handles the
    local side. Returns the newly-pruned records (already-pruned rows from
    a previous run are skipped, not re-returned).
    """
    state_db_path = Path(state_db_path).resolve()
    subscribed_uuids = {p.uuid for p in all_subscriptions}
    if lock_path is None:
        lock_path = state_db_path.parent / ".podcasts.lock"

    pruned: list[EpisodeRecord] = []
    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        for episode in db.list_episodes():
            if episode.unsubscribed or episode.podcast_uuid in subscribed_uuids:
                continue
            if not _other_profile_still_wants_show(episode.podcast_uuid, state_db_path):
                path = Path(episode.local_path)
                if path.is_file():
                    path.unlink()
            db.mark_unsubscribed(episode.episode_uuid)
            pruned.append(episode)
    return pruned


def push_pending_play_status(
    token: str,
    *,
    state_db_path: Path | str,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> tuple[list[EpisodeRecord], list[tuple[EpisodeRecord, str]]]:
    """Pushes every locally-confirmed play (`pending_push=1` — set by
    sync-orchestrator's on-device read-back, or a manual
    `StateDB.update_play_state` call) to Pocket Casts, clearing the flag
    on success.

    This existed only as the standalone `podcast-manager push-play-status`
    CLI command until 2026-08-18 -- never wired into the normal
    `music-stack sync --source podcasts` flow, so real on-device listening
    progress never actually reached Pocket Casts unless someone remembered
    to run that command by hand. `run_sync` now calls this before syncing
    episodes, so a routine podcast sync round-trips: push what the device
    already confirmed, then pull Pocket Casts' now-current state.

    Returns (pushed, failed) — failed as (episode, error message) pairs,
    same shape as sync_podcast's own per-episode failure handling, so one
    episode's push failing (e.g. a transient network blip) doesn't abort
    the rest of the batch."""
    if lock_path is None:
        lock_path = Path(state_db_path).parent / ".podcasts.lock"

    pushed: list[EpisodeRecord] = []
    failed: list[tuple[EpisodeRecord, str]] = []
    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        for episode in db.list_episodes_pending_push():
            try:
                update_episode_status(
                    token,
                    episode_uuid=episode.episode_uuid,
                    podcast_uuid=episode.podcast_uuid,
                    played=episode.played,
                    played_up_to=episode.played_up_to,
                )
            except httpx.HTTPError as exc:
                failed.append((episode, str(exc)))
                continue
            db.clear_pending_push(episode.episode_uuid)
            pushed.append(episode)
    return pushed, failed


@dataclass
class BackfillResult:
    updated: list[EpisodeRecord] = field(default_factory=list)
    unresolved_feeds: list[str] = field(default_factory=list)  # show titles
    unmatched: int = 0  # needed backfill, but not found in the resolved feed


def backfill_episode_metadata(
    subscriptions: list[PodcastSummary],
    *,
    state_db_path: Path | str,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> BackfillResult:
    """One-off backfill for episodes downloaded before RSS-sourced
    metadata (description/episode/season number/published date) existed,
    or episodes sync_podcast()'s own candidate loop has since moved past
    -- record_episode() only ever (re)writes metadata for a show's
    *current* top-N candidates each run, so an already-played/archived
    episode still sitting locally (e.g. sync_unplayed_only=False, or not
    yet pruned) never gets touched by a normal sync again once it drops
    out of that window. This walks every locally-known episode instead,
    regardless of candidate status.

    Only resolves/fetches a show's feed if it actually has episodes
    needing backfill (all four metadata fields still blank/None) --
    skips the network entirely for shows already fully backfilled.
    Matched by audio_url against the feed's enclosure URLs, same as
    sync_podcast(). Best-effort per show: an unresolvable feed is
    recorded in `unresolved_feeds` and that show's episodes are simply
    left as-is, same resilience pattern used everywhere else in this
    module."""
    if lock_path is None:
        lock_path = Path(state_db_path).parent / ".podcasts.lock"

    result = BackfillResult()
    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        episodes_by_show: dict[str, list[EpisodeRecord]] = {}
        for episode in db.list_episodes():
            if (
                not episode.description
                and episode.episode_number is None
                and episode.season_number is None
                and not episode.published_at
            ):
                episodes_by_show.setdefault(episode.podcast_uuid, []).append(episode)

        for podcast in subscriptions:
            needing_backfill = episodes_by_show.get(podcast.uuid)
            if not needing_backfill:
                continue

            feed_url = resolve_feed_url(podcast.title, podcast.author)
            if not feed_url:
                result.unresolved_feeds.append(podcast.title)
                continue

            rss_by_enclosure_url = {ep.enclosure_url: ep for ep in fetch_rss_episodes(feed_url)}
            for episode in needing_backfill:
                rss_meta = rss_by_enclosure_url.get(episode.audio_url)
                if rss_meta is None:
                    result.unmatched += 1
                    continue
                db.update_episode_metadata(
                    episode.episode_uuid,
                    description=rss_meta.description,
                    episode_number=rss_meta.episode_number,
                    season_number=rss_meta.season_number,
                    published_at=rss_meta.published or "",
                )
                result.updated.append(episode)

    return result
