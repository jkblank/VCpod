from __future__ import annotations

import re
from dataclasses import dataclass

from ytmusicapi import OAuthCredentials, YTMusic


def _oauth_credentials(client_id: str | None, client_secret: str | None) -> OAuthCredentials | None:
    # Without this, YTMusic(auth=oauth_path) can list/read fine on a
    # still-fresh token but raises YTMusicUserError the moment that
    # token needs refreshing -- ytmusicapi has no default/shared OAuth
    # client of its own, so the *same* client_id/secret the token was
    # minted with must be supplied on every use, not just at capture
    # time. See notes.md's ytmusic-oauth entry.
    if not client_id or not client_secret:
        return None
    return OAuthCredentials(client_id=client_id, client_secret=client_secret)

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


def _library_track_count(raw: object) -> int | None:
    # get_library_playlists' own "count" field comes from ytmusicapi
    # parsing YouTube Music's own UI subtitle text (parsers/browsing.py's
    # parse_playlist), not a real structured field -- confirmed against
    # the real ytmusicapi 1.12.1 source: it's only ever set when a
    # playlist's subtitle happens to have exactly a 3-run shape (title +
    # count + author), and is simply absent otherwise, which real
    # accounts hit often (e.g. any playlist with no description). A
    # missing dict key plus this project's own `.get("count", 0)` used
    # to silently read as a real "0 tracks" for every one of those,
    # rather than "unknown" -- confirmed live, this made the track count
    # column look broken/empty across most playlists in a real account.
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def list_playlists(
    oauth_path: str,
    limit: int | None = None,
    *,
    oauth_client_id: str | None = None,
    oauth_client_secret: str | None = None,
) -> list[PlaylistSummary]:
    # get_library_playlists needs an authenticated session — unlike
    # get_playlist_tracks below, there's no public/unauthenticated
    # equivalent for "the account's own library". Confirmed against the
    # real ytmusicapi 1.12.1 source (LibraryMixin.get_library_playlists).
    yt = YTMusic(
        auth=oauth_path,
        oauth_credentials=_oauth_credentials(oauth_client_id, oauth_client_secret),
    )
    playlists = yt.get_library_playlists(limit=limit)
    summaries = []
    for p in playlists:
        track_count = _library_track_count(p.get("count"))
        if track_count is None:
            # Same real trackCount field get_playlist_summary below
            # already relies on for a public playlist -- one extra
            # request, only for playlists the cheap listing call above
            # didn't already give a usable count for.
            try:
                track_count = yt.get_playlist(p["playlistId"], limit=1).get("trackCount", 0)
            except Exception:
                track_count = 0
        summaries.append(
            PlaylistSummary(
                source_id=p["playlistId"],
                name=p.get("title", ""),
                track_count=track_count,
                owner=None,
            )
        )
    return summaries


def get_playlist_summary(
    playlist_id: str,
    oauth_path: str | None = None,
    *,
    oauth_client_id: str | None = None,
    oauth_client_secret: str | None = None,
) -> PlaylistSummary:
    """Metadata for one playlist by id -- unlike list_playlists (which
    can only ever see the authenticated account's own library),
    get_playlist() works fully unauthenticated for a public playlist
    (same confirmed-live fact get_playlist_tracks below already relies
    on). This is what backs "add a playlist by its public share link"
    for playlists that aren't saved to your own account -- see
    music-stack-planning.md's "manual entry as fallback" design and
    web_gui_backend/routers/sources.py's resolve-by-url route.

    limit=1 (not the default 100, and not 0) -- only trackCount is
    needed, not the actual track list, but 0 wasn't confirmed safe
    against ytmusicapi's own handling and 1 costs nothing extra."""
    yt = YTMusic(
        auth=oauth_path,
        oauth_credentials=_oauth_credentials(oauth_client_id, oauth_client_secret),
    )
    playlist = yt.get_playlist(playlist_id, limit=1)
    return PlaylistSummary(
        source_id=playlist.get("id", playlist_id),
        name=playlist.get("title", ""),
        track_count=playlist.get("trackCount", 0),
        owner=(playlist.get("author") or {}).get("name"),
    )


def _artist_names(track: dict) -> str:
    return ", ".join(a["name"] for a in track.get("artists") or [] if a.get("name"))


def get_playlist_tracks(
    playlist_id: str,
    oauth_path: str | None = None,
    *,
    oauth_client_id: str | None = None,
    oauth_client_secret: str | None = None,
) -> list[TrackMeta]:
    # oauth_path is optional here: confirmed live against a real public
    # playlist that get_playlist() works completely unauthenticated —
    # only the account's own library listing (list_playlists above)
    # requires a session. See notes.md.
    yt = YTMusic(
        auth=oauth_path,
        oauth_credentials=_oauth_credentials(oauth_client_id, oauth_client_secret),
    )
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
