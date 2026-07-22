from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

LOGIN_URL = "https://api.pocketcasts.com/user/login"
PODCAST_LIST_URL = "https://api.pocketcasts.com/user/podcast/list"
PODCAST_EPISODES_URL = "https://api.pocketcasts.com/user/podcast/episodes"
PODCAST_FULL_URL = "https://cache.pocketcasts.com/podcast/full/{uuid}/0/3/1000"
UPDATE_EPISODE_URL = "https://api.pocketcasts.com/sync/update_episode"

UNPLAYED_STATUS = 1
IN_PROGRESS_STATUS = 2
PLAYED_STATUS = 3  # confirmed against a real account

# httpx's default 5s timeout produced spurious ConnectTimeout/ReadTimeout
# failures against these endpoints in practice — more generous everywhere,
# not just on the (separately timed) episode download.
_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=15.0, read=30.0)


@dataclass
class PodcastSummary:
    uuid: str
    title: str
    author: str


@dataclass
class EpisodeState:
    """Per-user play state. Confirmed against a real account: Pocket Casts
    only returns a row here for episodes the user has actually interacted
    with (played, started, etc.) — there is no row at all for an episode
    still in its default/untouched (i.e. unplayed) state."""

    uuid: str
    played: bool
    played_up_to: int


@dataclass
class FullEpisode:
    """An episode from the podcast's full catalog, including its direct
    downloadable audio URL. Confirmed against a real account — no RSS feed
    fetch/parse is needed at all, Pocket Casts already resolves it."""

    uuid: str
    title: str
    url: str
    published: str | None
    duration: int


def _first_present(item: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in item:
            return item[key]
    return default


def load_credentials(path: Path | str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["email"], data["password"]


def login(email: str, password: str) -> str:
    resp = httpx.post(
        LOGIN_URL,
        json={"email": email, "password": password, "scope": "webplayer"},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def list_subscriptions(token: str) -> list[PodcastSummary]:
    resp = httpx.post(
        PODCAST_LIST_URL, headers=_auth_headers(token), json={"v": "1"}, timeout=_REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        PodcastSummary(uuid=item["uuid"], title=item.get("title", ""), author=item.get("author", ""))
        for item in data.get("podcasts", [])
    ]


def resolve_show_selection(
    subscriptions: list[PodcastSummary], wanted: list[str]
) -> tuple[list[PodcastSummary], list[str]]:
    """Match each `wanted` entry against subscriptions by exact UUID or
    case-insensitive title, so callers/users don't have to know a show's
    Pocket Casts UUID just to target it by name. Returns (matched
    subscriptions in subscription order, deduped; the `wanted` entries that
    matched nothing) so callers can warn on typos instead of silently
    syncing nothing.
    """
    by_uuid = {p.uuid: p for p in subscriptions}
    by_title = {p.title.casefold(): p for p in subscriptions}

    matched: list[PodcastSummary] = []
    matched_uuids: set[str] = set()
    unmatched: list[str] = []
    for name in wanted:
        podcast = by_uuid.get(name) or by_title.get(name.casefold())
        if podcast is None:
            unmatched.append(name)
        elif podcast.uuid not in matched_uuids:
            matched.append(podcast)
            matched_uuids.add(podcast.uuid)
    return matched, unmatched


def list_episode_states(token: str, podcast_uuid: str) -> list[EpisodeState]:
    resp = httpx.post(
        PODCAST_EPISODES_URL,
        headers=_auth_headers(token),
        json={"uuid": podcast_uuid, "page": 1, "sort": 3},
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    states: list[EpisodeState] = []
    for item in data.get("episodes", []):
        status_value = _first_present(item, "playingStatus", "playing_status", default=0)
        played_up_to = _first_present(item, "playedUpTo", "played_up_to", default=0)
        states.append(
            EpisodeState(
                uuid=item["uuid"],
                played=status_value == PLAYED_STATUS,
                played_up_to=played_up_to or 0,
            )
        )
    return states


def update_episode_status(
    token: str, *, episode_uuid: str, podcast_uuid: str, played: bool, played_up_to: int
) -> None:
    """Pushes device-derived play state back to Pocket Casts. Endpoint and
    status values found via a current third-party client using the same
    api.pocketcasts.com domain/bearer-auth this project's own read calls
    are already confirmed against.

    Live-verified against a real account with a genuine before/after
    state transition (not just re-sending an already-matching value,
    which would have silently passed even if broken): status
    (played/unplayed/in-progress) reliably takes effect. played_up_to
    does NOT — confirmed with both snake_case and camelCase field names,
    both accepted with 200 OK but the position silently stays unchanged.
    The real iOS app's sync protocol uses Protocol Buffers in places
    (confirmed via Automattic/pocket-casts-ios, the open-source client);
    position sync specifically may require that instead of this simple
    JSON endpoint. Still sent here (harmless, and future-proofs for if
    it ever does start working) but do not rely on it — only `played`
    is confirmed reliable. See notes.md's M8 write-up."""
    if played:
        status = PLAYED_STATUS
    elif played_up_to > 0:
        status = IN_PROGRESS_STATUS
    else:
        status = UNPLAYED_STATUS
    resp = httpx.post(
        UPDATE_EPISODE_URL,
        headers=_auth_headers(token),
        json={
            "uuid": episode_uuid,
            "podcast": podcast_uuid,
            "status": status,
            "played_up_to": played_up_to,
        },
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()


def list_full_episodes(token: str, podcast_uuid: str) -> list[FullEpisode]:
    url = PODCAST_FULL_URL.format(uuid=podcast_uuid)
    resp = httpx.get(
        url, headers=_auth_headers(token), follow_redirects=True, timeout=_REQUEST_TIMEOUT
    )
    resp.raise_for_status()
    data = resp.json()
    podcast = data.get("podcast") or {}
    episodes: list[FullEpisode] = []
    for item in podcast.get("episodes", []):
        audio_url = item.get("url")
        if not audio_url:
            continue
        episodes.append(
            FullEpisode(
                uuid=item["uuid"],
                title=item.get("title", ""),
                url=audio_url,
                published=item.get("published"),
                duration=item.get("duration", 0),
            )
        )
    return episodes
