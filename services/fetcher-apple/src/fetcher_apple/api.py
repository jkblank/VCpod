from __future__ import annotations

import asyncio
from dataclasses import dataclass

from gamdl.api import AppleMusicApi

from fetcher_apple._net import force_ipv4_dns


@dataclass
class PlaylistSummary:
    source_id: str
    name: str
    track_count: int
    owner: str | None


@dataclass
class TrackMeta:
    source_id: str
    title: str
    artist: str


def _playlist_source_id(item: dict) -> str:
    # `playParams.globalId` is the catalog-style `pl.*` id (what playlist URLs
    # use, and what profile configs store); `playParams.id`/top-level `id` are
    # the library-internal `p.*` id, which isn't addressable the same way.
    # Confirmed against a real account's get_library_playlists response.
    play_params = item.get("attributes", {}).get("playParams") or {}
    return play_params.get("globalId") or play_params.get("id") or item["id"]


# Bounds how many of the per-playlist track-count requests below run at
# once -- unbounded concurrency across a real library's full playlist
# count (tens to low hundreds) risks tripping Apple's own rate limiting;
# fully sequential would make a large library noticeably slow to list.
_TRACK_COUNT_CONCURRENCY = 8


async def _real_track_count(
    api: AppleMusicApi, semaphore: asyncio.Semaphore, library_id: str
) -> int:
    # get_library_playlists' own "trackCount" attribute never actually
    # exists in its response at all -- confirmed live against a real
    # account/the real gamdl response shape, not just absent sometimes
    # like ytmusicapi's equivalent gap (see fetcher_ytmusic/api.py's
    # matching fix). The only way to get a real count is this one extra
    # per-playlist request -- include="tracks" is only valid on a
    # single-resource fetch (confirmed live: the bulk list endpoint
    # 400s on it), and relationships.tracks.meta.total gives the real
    # total without needing to fetch more than one actual track
    # (limit=1). Must use the library-internal `p.*` id here (item["id"],
    # the raw API id), not the `pl.*` catalog id _playlist_source_id
    # prefers -- get_library_playlist 404s on the latter. See notes.md.
    async with semaphore:
        try:
            detail = await api.get_library_playlist(library_id, include="tracks", limit=1)
        except Exception:
            return 0
    data = detail.get("data") or []
    if not data:
        return 0
    tracks_rel = data[0].get("relationships", {}).get("tracks", {})
    return tracks_rel.get("meta", {}).get("total", 0)


async def _list_playlists_async(
    cookies_path: str, limit: int = 100
) -> list[PlaylistSummary]:
    api = await AppleMusicApi.create_from_netscape_cookies(cookies_path=cookies_path)
    items: list[dict] = []
    offset = 0
    while True:
        page = await api.get_library_playlists(limit=limit, offset=offset)
        page_items = page.get("data", [])
        if not page_items:
            break
        items.extend(page_items)
        if len(page_items) < limit:
            break
        offset += limit

    semaphore = asyncio.Semaphore(_TRACK_COUNT_CONCURRENCY)
    track_counts = await asyncio.gather(
        *(_real_track_count(api, semaphore, item["id"]) for item in items)
    )

    return [
        PlaylistSummary(
            source_id=_playlist_source_id(item),
            name=item.get("attributes", {}).get("name", ""),
            track_count=track_count,
            owner=item.get("attributes", {}).get("curatorName"),
        )
        for item, track_count in zip(items, track_counts)
    ]


def list_playlists(cookies_path: str, limit: int = 100) -> list[PlaylistSummary]:
    force_ipv4_dns()
    return asyncio.run(_list_playlists_async(cookies_path, limit=limit))


async def _get_playlist_tracks_async(
    cookies_path: str, source_id: str
) -> list[TrackMeta]:
    api = await AppleMusicApi.create_from_netscape_cookies(cookies_path=cookies_path)
    # source_id is always the catalog-style `pl.*` id (what configs store and
    # what list_playlists returns), so this must use the catalog endpoint —
    # get_library_playlist expects the different, non-portable library-internal
    # `p.*` id instead and 404s on a `pl.*` id.
    playlist = await api.get_playlist(source_id)
    data = playlist.get("data", [])
    if not data:
        return []
    tracks = data[0].get("relationships", {}).get("tracks", {}).get("data", [])
    result: list[TrackMeta] = []
    for track in tracks:
        attrs = track.get("attributes", {})
        result.append(
            TrackMeta(
                source_id=track["id"],
                title=attrs.get("name", ""),
                artist=attrs.get("artistName", ""),
            )
        )
    return result


def get_playlist_tracks(cookies_path: str, source_id: str) -> list[TrackMeta]:
    force_ipv4_dns()
    return asyncio.run(_get_playlist_tracks_async(cookies_path, source_id))
