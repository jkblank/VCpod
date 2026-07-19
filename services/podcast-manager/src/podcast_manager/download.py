from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from common.state import EpisodeRecord, StateDB

from podcast_manager.api import (
    FullEpisode,
    PodcastSummary,
    list_episode_states,
    list_full_episodes,
)

_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize(text: str) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", text).strip()
    return cleaned or "Untitled"


def _guess_extension(url: str) -> str:
    suffix = Path(urlparse(url).path).suffix
    return suffix if suffix else ".mp3"


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
) -> SyncResult:
    library_root = Path(library_root)
    result = SyncResult()

    full_episodes = list_full_episodes(token, podcast.uuid)
    states_by_uuid = {s.uuid: s for s in list_episode_states(token, podcast.uuid)}

    candidates = sorted(full_episodes, key=lambda e: e.published or "", reverse=True)

    if sync_unplayed_only:
        candidates = [
            e
            for e in candidates
            if not (states_by_uuid.get(e.uuid) and states_by_uuid[e.uuid].played)
        ]

    candidates = candidates[:max_episodes_per_show]
    if not candidates:
        return result

    show_dir = library_root / _sanitize(podcast.title)

    with StateDB(state_db_path) as db:
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
            state = states_by_uuid.get(episode.uuid)
            record = EpisodeRecord(
                episode_uuid=episode.uuid,
                podcast_uuid=podcast.uuid,
                show_name=podcast.title,
                local_path=str(dest),
                played=bool(state and state.played),
                played_up_to=state.played_up_to if state else 0,
                downloaded_at=datetime.now(timezone.utc).isoformat(),
                title=episode.title,
                audio_url=episode.url,
                duration_seconds=episode.duration,
            )
            db.record_episode(record)
            (result.already_present if already_downloaded else result.downloaded).append(record)

    return result
