from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from common.config import save_global_config
from common.models import (
    AppleMusicSource,
    GlobalConfig,
    LibraryManagerConfig,
    Paths,
    PocketCastsGlobalConfig,
    PodcastsGlobalConfig,
    SourcesConfig,
    SpotifySource,
    YtMusicSource,
)
from web_gui_backend import routers
from web_gui_backend.app import create_app

VALID_APPLE_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".music.apple.com\tTRUE\t/\tTRUE\t2145916800\tmedia-user-token\tabc123token\n"
    ".music.apple.com\tTRUE\t/\tTRUE\t2145916800\titua\tUS\n"
)
MISSING_TOKEN_APPLE_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".music.apple.com\tTRUE\t/\tTRUE\t2145916800\titua\tUS\n"
)
VALID_YT_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t2145916800\tSID\tabc123\n"
)


@dataclass
class _FakePlaylist:
    source_id: str
    name: str
    track_count: int
    owner: str | None


def _global_config() -> GlobalConfig:
    return GlobalConfig(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(
                enabled=True, cookies_file="/config/secrets/apple_music_cookies.txt"
            ),
            spotify=SpotifySource(
                enabled=False, credentials_file="/config/secrets/spotify_credentials.json"
            ),
            ytmusic=YtMusicSource(
                enabled=True,
                oauth_file="/config/secrets/ytmusic_oauth.json",
                cookies_file="/config/secrets/youtube_cookies.txt",
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
        library_manager=LibraryManagerConfig(),
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    save_global_config(_global_config(), tmp_path / "global.yaml")
    return tmp_path


@pytest.fixture
def client(config_root) -> TestClient:
    app = create_app(config_root=config_root)
    return TestClient(app)


def test_apple_music_playlists_returns_listed_playlists(monkeypatch, client):
    fake = [_FakePlaylist("pl.1", "Workout", 12, "me"), _FakePlaylist("pl.2", "Chill", 5, None)]
    monkeypatch.setattr(routers.sources, "list_apple_music_playlists", lambda path: fake)

    resp = client.get("/api/sources/apple-music/playlists")

    assert resp.status_code == 200
    assert resp.json() == [
        {"source_id": "pl.1", "name": "Workout", "track_count": 12, "owner": "me"},
        {"source_id": "pl.2", "name": "Chill", "track_count": 5, "owner": None},
    ]


def test_apple_music_playlists_returns_502_on_failure(monkeypatch, client):
    def _boom(path):
        raise ValueError('"media-user-token" cookie not found')

    monkeypatch.setattr(routers.sources, "list_apple_music_playlists", _boom)

    resp = client.get("/api/sources/apple-music/playlists")

    assert resp.status_code == 502
    assert "media-user-token" in resp.json()["detail"]


def test_apple_music_playlists_resolves_config_rooted_cookies_path(monkeypatch, client, config_root):
    captured = {}

    def _capture(path):
        captured["path"] = path
        return []

    monkeypatch.setattr(routers.sources, "list_apple_music_playlists", _capture)

    client.get("/api/sources/apple-music/playlists")

    assert captured["path"] == str(config_root / "secrets" / "apple_music_cookies.txt")


def test_ytmusic_playlists_returns_listed_playlists(monkeypatch, client):
    fake = [_FakePlaylist("PL1", "Commute", 8, None)]
    monkeypatch.setattr(routers.sources, "list_ytmusic_playlists", lambda path: fake)

    resp = client.get("/api/sources/ytmusic/playlists")

    assert resp.status_code == 200
    assert resp.json() == [{"source_id": "PL1", "name": "Commute", "track_count": 8, "owner": None}]


def test_resolve_ytmusic_playlist_accepts_bare_id(monkeypatch, client):
    captured = {}

    def _capture(playlist_id, oauth_path=None):
        captured["playlist_id"] = playlist_id
        captured["oauth_path"] = oauth_path
        return _FakePlaylist("PLxyz", "New EDM This Week", 237, "sigmatics")

    monkeypatch.setattr(routers.sources, "get_ytmusic_playlist_summary", _capture)

    resp = client.get("/api/sources/ytmusic/resolve", params={"url": "PLxyz"})

    assert resp.status_code == 200
    assert resp.json() == {
        "source_id": "PLxyz", "name": "New EDM This Week", "track_count": 237, "owner": "sigmatics",
    }
    assert captured == {"playlist_id": "PLxyz", "oauth_path": None}


def test_resolve_ytmusic_playlist_extracts_id_from_music_youtube_share_link(monkeypatch, client):
    captured = {}

    def _capture(playlist_id, oauth_path=None):
        captured["playlist_id"] = playlist_id
        return _FakePlaylist(playlist_id, "x", 1, None)

    monkeypatch.setattr(routers.sources, "get_ytmusic_playlist_summary", _capture)

    resp = client.get(
        "/api/sources/ytmusic/resolve",
        params={"url": "https://music.youtube.com/playlist?list=PLxyz123&si=abc"},
    )

    assert resp.status_code == 200
    assert captured["playlist_id"] == "PLxyz123"


def test_resolve_ytmusic_playlist_extracts_id_from_plain_youtube_share_link(monkeypatch, client):
    captured = {}

    def _capture(playlist_id, oauth_path=None):
        captured["playlist_id"] = playlist_id
        return _FakePlaylist(playlist_id, "x", 1, None)

    monkeypatch.setattr(routers.sources, "get_ytmusic_playlist_summary", _capture)

    resp = client.get(
        "/api/sources/ytmusic/resolve",
        params={"url": "https://www.youtube.com/playlist?list=PLxyz123"},
    )

    assert resp.status_code == 200
    assert captured["playlist_id"] == "PLxyz123"


def test_resolve_ytmusic_playlist_rejects_url_without_list_param(client):
    resp = client.get(
        "/api/sources/ytmusic/resolve", params={"url": "https://music.youtube.com/playlist"}
    )

    assert resp.status_code == 422
    assert "list=" in resp.json()["detail"]


def test_resolve_ytmusic_playlist_rejects_empty_input(client):
    resp = client.get("/api/sources/ytmusic/resolve", params={"url": "   "})
    assert resp.status_code == 422


def test_resolve_ytmusic_playlist_returns_502_when_playlist_not_found(monkeypatch, client):
    def _boom(playlist_id, oauth_path=None):
        raise ValueError("playlist not found")

    monkeypatch.setattr(routers.sources, "get_ytmusic_playlist_summary", _boom)

    resp = client.get("/api/sources/ytmusic/resolve", params={"url": "PLdoesnotexist"})

    assert resp.status_code == 502


def test_put_apple_music_cookies_accepts_valid_file(client, config_root):
    resp = client.put(
        "/api/sources/apple-music/cookies", json={"cookies_txt": VALID_APPLE_COOKIES}
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    written = config_root / "secrets" / "apple_music_cookies.txt"
    assert written.read_text() == VALID_APPLE_COOKIES


def test_put_apple_music_cookies_rejects_missing_token(client, config_root):
    resp = client.put(
        "/api/sources/apple-music/cookies", json={"cookies_txt": MISSING_TOKEN_APPLE_COOKIES}
    )

    assert resp.status_code == 422
    assert "media-user-token" in resp.json()["detail"]
    assert not (config_root / "secrets" / "apple_music_cookies.txt").exists()


def test_put_apple_music_cookies_rejects_garbage(client, config_root):
    resp = client.put("/api/sources/apple-music/cookies", json={"cookies_txt": "not a cookie file"})

    assert resp.status_code == 422
    assert not (config_root / "secrets" / "apple_music_cookies.txt").exists()


def test_put_apple_music_cookies_never_overwrites_working_file_with_bad_paste(client, config_root):
    # First a real, valid save...
    client.put("/api/sources/apple-music/cookies", json={"cookies_txt": VALID_APPLE_COOKIES})
    # ...then a bad paste must not clobber it.
    resp = client.put("/api/sources/apple-music/cookies", json={"cookies_txt": "garbage"})

    assert resp.status_code == 422
    written = config_root / "secrets" / "apple_music_cookies.txt"
    assert written.read_text() == VALID_APPLE_COOKIES


def test_put_ytmusic_cookies_accepts_valid_file_without_apple_token(client, config_root):
    resp = client.put("/api/sources/ytmusic/cookies", json={"cookies_txt": VALID_YT_COOKIES})

    assert resp.status_code == 200
    written = config_root / "secrets" / "youtube_cookies.txt"
    assert written.read_text() == VALID_YT_COOKIES


def test_sources_status_reports_missing_files(client):
    resp = client.get("/api/sources/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["apple_music"] == {"enabled": True, "exists": False, "updated_at": None}
    assert body["spotify"]["enabled"] is False


def test_sources_status_reports_existing_file(client, config_root):
    client.put("/api/sources/apple-music/cookies", json={"cookies_txt": VALID_APPLE_COOKIES})

    resp = client.get("/api/sources/status")

    apple = resp.json()["apple_music"]
    assert apple["exists"] is True
    assert isinstance(apple["updated_at"], float)
