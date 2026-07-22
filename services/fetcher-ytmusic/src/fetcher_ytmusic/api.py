from __future__ import annotations

import re
from dataclasses import dataclass

from ytmusicapi import YTMusic

# YouTube's own thumbnail API only ever returns small (60x60/120x120)
# images from ytmusicapi — but the URLs are a Google image-proxy scheme
# that accepts an arbitrary requested size via this w/h suffix, confirmed
# live: rewriting e.g. "...=w120-h120-l90-rj" to "...=w1200-h1200-l90-rj"
# against the same URL returns a real, much larger image (~190KB vs a few
# KB), not an error or a re-scaled-up blurry copy. 1200 matches roughly
# what real Apple Music embedded covers look like (a few hundred KB to ~1MB).
_THUMBNAIL_SIZE_RE = re.compile(r"=w\d+-h\d+")
_TARGET_THUMBNAIL_SIZE = 1200


def _best_thumbnail_url(thumbnails: list[dict] | None) -> str | None:
    if not thumbnails:
        return None
    largest = max(thumbnails, key=lambda t: t.get("width", 0))
    url = largest.get("url")
    if not url:
        return None
    upscaled, count = _THUMBNAIL_SIZE_RE.subn(
        f"=w{_TARGET_THUMBNAIL_SIZE}-h{_TARGET_THUMBNAIL_SIZE}", url, count=1
    )
    return upscaled if count else url


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
    album: str
    thumbnail_url: str | None = None


def list_playlists(oauth_path: str, limit: int | None = None) -> list[PlaylistSummary]:
    # get_library_playlists needs an authenticated session — unlike
    # get_playlist_tracks below, there's no public/unauthenticated
    # equivalent for "the account's own library". Confirmed against the
    # real ytmusicapi 1.12.1 source (LibraryMixin.get_library_playlists).
    yt = YTMusic(auth=oauth_path)
    playlists = yt.get_library_playlists(limit=limit)
    return [
        PlaylistSummary(
            source_id=p["playlistId"],
            name=p.get("title", ""),
            track_count=p.get("count", 0),
            owner=None,
        )
        for p in playlists
    ]


def _artist_names(track: dict) -> str:
    return ", ".join(a["name"] for a in track.get("artists") or [] if a.get("name"))


def get_playlist_tracks(playlist_id: str, oauth_path: str | None = None) -> list[TrackMeta]:
    # oauth_path is optional here: confirmed live against a real public
    # playlist that get_playlist() works completely unauthenticated —
    # only the account's own library listing (list_playlists above)
    # requires a session. See notes.md.
    yt = YTMusic(auth=oauth_path)
    playlist = yt.get_playlist(playlist_id, limit=None)
    tracks: list[TrackMeta] = []
    for track in playlist.get("tracks", []):
        video_id = track.get("videoId")
        if not video_id or track.get("isAvailable") is False:
            continue
        album = (track.get("album") or {}).get("name")
        tracks.append(
            TrackMeta(
                source_id=video_id,
                title=track.get("title", ""),
                artist=_artist_names(track),
                # Singles/uploads with no album grouping still need a
                # library folder — mirrors gamdl's own "{title} - Single"
                # convention already seen throughout the real library.
                album=album or f"{track.get('title', '')} - Single",
                thumbnail_url=_best_thumbnail_url(track.get("thumbnails")),
            )
        )
    return tracks
