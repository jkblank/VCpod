from __future__ import annotations

import json
from dataclasses import dataclass

import httpx
from librespot.core import Session
from librespot.oauth import OAuth

SCOPES = (
    "playlist-read-private",
    "user-read-email",
    "user-library-read",
    "user-follow-read",
)

ME_PLAYLISTS_URL = "https://api.spotify.com/v1/me/playlists"
PLAYLISTS_URL = "https://api.spotify.com/v1/playlists"


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
    track_number: int
    isrc: str | None = None


def _build_session(credentials_path: str) -> Session:
    # As of the migration to Googolplexed0/zotify (login5 auth fix, see
    # notes.md), credentials saved via an interactive login are OAuth PKCE
    # tokens (a JSON dict: client_id/access_token/refresh_token/expires_at)
    # — a different format from the legacy raw stored-credentials blob
    # Session.Builder.stored_file() expects. Mirrors zotify's own
    # Zotify.login() branching (zotify/config.py) so credentials created
    # via zotify's interactive login work here too.
    conf = Session.Configuration.Builder().set_store_credentials(False).build()
    builder = Session.Builder(conf)
    with open(credentials_path, encoding="utf-8") as f:
        creds = json.load(f)
    if creds.get("type") == OAuth.OAUTH_PKCE_TOKEN:
        oauth = OAuth(creds["client_id"], "", None).ingest_token_response(creds)
        oauth.refresh_token()
        builder.login_credentials = oauth.get_credentials()
    else:
        builder.stored_file(str(credentials_path))
    return builder.create()


def _get_access_token(credentials_path: str) -> str:
    session = _build_session(credentials_path)
    return session.tokens().get_token(*SCOPES).access_token


def _auth_headers(credentials_path: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_get_access_token(credentials_path)}"}


def list_playlists(credentials_path: str) -> list[PlaylistSummary]:
    headers = _auth_headers(credentials_path)
    summaries: list[PlaylistSummary] = []
    url: str | None = f"{ME_PLAYLISTS_URL}?limit=50"
    with httpx.Client() as client:
        while url:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                owner = (item.get("owner") or {}).get("display_name")
                tracks = item.get("tracks") or {}
                summaries.append(
                    PlaylistSummary(
                        source_id=item["id"],
                        name=item.get("name", ""),
                        track_count=tracks.get("total", 0),
                        owner=owner,
                    )
                )
            url = data.get("next")
    return summaries


def get_playlist_tracks(credentials_path: str, source_id: str) -> list[TrackMeta]:
    headers = _auth_headers(credentials_path)
    result: list[TrackMeta] = []
    url: str | None = f"{PLAYLISTS_URL}/{source_id}/tracks?limit=100"
    with httpx.Client() as client:
        while url:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("items", []):
                track = item.get("track")
                if not track or not track.get("id"):
                    continue
                artists = track.get("artists") or []
                artist_name = artists[0]["name"] if artists else ""
                album_name = (track.get("album") or {}).get("name", "")
                isrc = (track.get("external_ids") or {}).get("isrc")
                result.append(
                    TrackMeta(
                        source_id=track["id"],
                        title=track.get("name", ""),
                        artist=artist_name,
                        album=album_name,
                        track_number=track.get("track_number", 0),
                        isrc=isrc,
                    )
                )
            url = data.get("next")
    return result
