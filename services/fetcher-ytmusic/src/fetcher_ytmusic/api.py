from __future__ import annotations

from dataclasses import dataclass

from ytmusicapi import YTMusic


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
            )
        )
    return tracks
