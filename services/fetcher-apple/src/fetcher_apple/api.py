from __future__ import annotations

import asyncio
from dataclasses import dataclass

from gamdl.api import AppleMusicApi


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


async def _list_playlists_async(
    cookies_path: str, limit: int = 100
) -> list[PlaylistSummary]:
    api = await AppleMusicApi.create_from_netscape_cookies(cookies_path=cookies_path)
    summaries: list[PlaylistSummary] = []
    offset = 0
    while True:
        page = await api.get_library_playlists(limit=limit, offset=offset)
        items = page.get("data", [])
        if not items:
            break
        for item in items:
            attrs = item.get("attributes", {})
            summaries.append(
                PlaylistSummary(
                    source_id=_playlist_source_id(item),
                    name=attrs.get("name", ""),
                    track_count=attrs.get("trackCount", 0),
                    owner=attrs.get("curatorName"),
                )
            )
        if len(items) < limit:
            break
        offset += limit
    return summaries


def list_playlists(cookies_path: str, limit: int = 100) -> list[PlaylistSummary]:
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
    return asyncio.run(_get_playlist_tracks_async(cookies_path, source_id))
