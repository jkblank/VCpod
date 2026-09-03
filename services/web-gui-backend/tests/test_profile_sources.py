import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from common.config import save_global_config, save_profile_config
from common.models import (
    AppleMusicSource,
    DeviceMatch,
    GlobalConfig,
    LibraryManagerConfig,
    Paths,
    PocketCastsGlobalConfig,
    PodcastsGlobalConfig,
    ProfileAppleMusicOverride,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    ProfileSourcesConfig,
    ProfileYtMusicOverride,
    SourcesConfig,
    SpotifySource,
    SyncSettings,
    YtMusicSource,
)
from web_gui_backend import routers
from web_gui_backend.app import create_app

VALID_APPLE_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".music.apple.com\tTRUE\t/\tTRUE\t2145916800\tmedia-user-token\tabc123token\n"
)
VALID_YT_COOKIES = (
    "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t2145916800\tSID\tabc123\n"
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
                oauth_client_file="/config/secrets/ytmusic_oauth_client.json",
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
        library_manager=LibraryManagerConfig(),
    )


def _profile(name: str, **overrides) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="serial", match_value=f"SERIAL-{name}"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file=f"/config/secrets/pocketcasts/{name}.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
        **overrides,
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    save_global_config(_global_config(), tmp_path / "global.yaml")
    save_profile_config(_profile("alice"), tmp_path / "profiles" / "alice.yaml")
    save_profile_config(_profile("bob"), tmp_path / "profiles" / "bob.yaml")
    return tmp_path


@pytest.fixture
def client(config_root) -> TestClient:
    app = create_app(config_root=config_root)
    return TestClient(app)


# --- status -------------------------------------------------------------


def test_status_reports_global_when_no_override(client):
    resp = client.get("/api/profiles/alice/sources/status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["apple_music"]["using"] == "global"
    assert body["apple_music"]["exists"] is False
    assert body["ytmusic"]["cookies"]["using"] == "global"
    assert body["ytmusic"]["oauth"]["using"] == "global"
    assert body["ytmusic"]["oauth_client"]["using"] == "global"


def test_status_404s_for_unknown_profile(client):
    resp = client.get("/api/profiles/nobody/sources/status")
    assert resp.status_code == 404


def test_status_reports_override_and_real_file(client, config_root):
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/alice/apple_music_cookies.txt"
                )
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )
    override_path = config_root / "secrets" / "alice" / "apple_music_cookies.txt"
    override_path.parent.mkdir(parents=True)
    override_path.write_text(VALID_APPLE_COOKIES)

    resp = client.get("/api/profiles/alice/sources/status")

    body = resp.json()["apple_music"]
    assert body["using"] == "override"
    assert body["exists"] is True
    assert isinstance(body["updated_at"], float)
    # The shared global file was never touched or reported as existing.
    assert not (config_root / "secrets" / "apple_music_cookies.txt").exists()


# --- playlists (override-or-global resolution) ---------------------------


def test_apple_music_playlists_uses_global_cookies_when_no_override(monkeypatch, client):
    captured = {}

    def _capture(path):
        captured["path"] = path
        return [_FakePlaylist("pl.1", "Chill", 5, None)]

    monkeypatch.setattr(routers.profile_sources, "list_apple_music_playlists", _capture)

    resp = client.get("/api/profiles/alice/sources/apple-music/playlists")

    assert resp.status_code == 200
    assert resp.json() == [{"source_id": "pl.1", "name": "Chill", "track_count": 5, "owner": None}]
    assert captured["path"].endswith("apple_music_cookies.txt")
    assert "alice" not in captured["path"]


def test_apple_music_playlists_uses_profile_override_cookies(monkeypatch, client, config_root):
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/alice/apple_music_cookies.txt"
                )
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )
    captured = {}

    def _capture(path):
        captured["path"] = path
        return []

    monkeypatch.setattr(routers.profile_sources, "list_apple_music_playlists", _capture)

    client.get("/api/profiles/alice/sources/apple-music/playlists")

    assert captured["path"] == str(config_root / "secrets" / "alice" / "apple_music_cookies.txt")


def test_apple_music_playlists_returns_502_on_failure(monkeypatch, client):
    monkeypatch.setattr(
        routers.profile_sources,
        "list_apple_music_playlists",
        lambda path: (_ for _ in ()).throw(ValueError("cookies expired")),
    )

    resp = client.get("/api/profiles/alice/sources/apple-music/playlists")

    assert resp.status_code == 502
    assert "cookies expired" in resp.json()["detail"]


# --- cookie capture writes an override -----------------------------------


def test_put_apple_music_cookies_writes_per_profile_file_and_sets_override(client, config_root):
    resp = client.put(
        "/api/profiles/alice/sources/apple-music/cookies", json={"cookies_txt": VALID_APPLE_COOKIES}
    )

    assert resp.status_code == 200
    written = config_root / "secrets" / "alice" / "apple_music_cookies.txt"
    assert written.read_text() == VALID_APPLE_COOKIES

    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded["sources"]["apple_music"]["cookies_file"] == (
        "/config/secrets/alice/apple_music_cookies.txt"
    )
    # The shared global file is never touched by a profile-scoped capture.
    assert not (config_root / "secrets" / "apple_music_cookies.txt").exists()


def test_put_apple_music_cookies_rejects_invalid_content_and_writes_nothing(client, config_root):
    resp = client.put(
        "/api/profiles/alice/sources/apple-music/cookies", json={"cookies_txt": "garbage"}
    )

    assert resp.status_code == 422
    assert not (config_root / "secrets" / "alice" / "apple_music_cookies.txt").exists()
    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded.get("sources") is None


def test_put_ytmusic_cookies_preserves_existing_oauth_override(client, config_root):
    # Setting cookies_file must not clobber an oauth_file this profile
    # already had overridden.
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                ytmusic=ProfileYtMusicOverride(oauth_file="/config/secrets/alice/ytmusic_oauth.json")
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )

    resp = client.put(
        "/api/profiles/alice/sources/ytmusic/cookies", json={"cookies_txt": VALID_YT_COOKIES}
    )

    assert resp.status_code == 200
    reloaded = client.get("/api/profiles/alice").json()
    yt = reloaded["sources"]["ytmusic"]
    assert yt["cookies_file"] == "/config/secrets/alice/youtube_cookies.txt"
    assert yt["oauth_file"] == "/config/secrets/alice/ytmusic_oauth.json"


def test_put_ytmusic_oauth_client_sets_override(client, config_root):
    resp = client.put(
        "/api/profiles/alice/sources/ytmusic/oauth-client",
        json={"client_id": "abc", "client_secret": "shh"},
    )

    assert resp.status_code == 200
    written = config_root / "secrets" / "alice" / "ytmusic_oauth_client.json"
    assert json.loads(written.read_text()) == {"client_id": "abc", "client_secret": "shh"}
    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded["sources"]["ytmusic"]["oauth_client_file"] == (
        "/config/secrets/alice/ytmusic_oauth_client.json"
    )


def test_put_ytmusic_oauth_client_rejects_missing_fields(client, config_root):
    resp = client.put(
        "/api/profiles/alice/sources/ytmusic/oauth-client", json={"client_id": "abc"}
    )
    assert resp.status_code == 422
    assert not (config_root / "secrets" / "alice" / "ytmusic_oauth_client.json").exists()


# --- device-code flow -----------------------------------------------------


def test_start_oauth_requires_a_client_saved_somewhere(client):
    resp = client.post("/api/profiles/alice/sources/ytmusic/oauth/start")
    assert resp.status_code == 422


def test_start_oauth_falls_back_to_global_client(monkeypatch, client, config_root):
    client_path = config_root / "secrets" / "ytmusic_oauth_client.json"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    client_path.write_text(json.dumps({"client_id": "global-id", "client_secret": "global-secret"}))
    captured = {}

    def _capture(client_id, client_secret):
        captured["client_id"] = client_id
        captured["client_secret"] = client_secret
        from fetcher_ytmusic.oauth import DeviceCodeStart

        return DeviceCodeStart(
            device_code="dev1", user_code="ABCD", verification_url="https://x", expires_in=1800, interval=5
        )

    monkeypatch.setattr(routers.profile_sources, "start_device_flow", _capture)

    resp = client.post("/api/profiles/alice/sources/ytmusic/oauth/start")

    assert resp.status_code == 200
    assert captured == {"client_id": "global-id", "client_secret": "global-secret"}


def test_poll_oauth_writes_per_profile_token_even_with_global_client(monkeypatch, client, config_root):
    client_path = config_root / "secrets" / "ytmusic_oauth_client.json"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    client_path.write_text(json.dumps({"client_id": "global-id", "client_secret": "global-secret"}))
    token = {
        "scope": "s", "token_type": "Bearer", "access_token": "at",
        "refresh_token": "rt", "expires_at": 123, "expires_in": 456,
    }
    monkeypatch.setattr(
        routers.profile_sources,
        "poll_device_flow",
        lambda client_id, client_secret, device_code: token,
    )

    resp = client.post(
        "/api/profiles/alice/sources/ytmusic/oauth/poll", json={"device_code": "dev1"}
    )

    assert resp.status_code == 200
    written = config_root / "secrets" / "alice" / "ytmusic_oauth.json"
    assert json.loads(written.read_text()) == token
    # global.yaml's own oauth_file was never touched.
    assert not (config_root / "secrets" / "ytmusic_oauth.json").exists()
    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded["sources"]["ytmusic"]["oauth_file"] == "/config/secrets/alice/ytmusic_oauth.json"


def test_poll_oauth_returns_pending(monkeypatch, client, config_root):
    client_path = config_root / "secrets" / "ytmusic_oauth_client.json"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    client_path.write_text(json.dumps({"client_id": "a", "client_secret": "b"}))
    from fetcher_ytmusic.oauth import OAuthPending

    monkeypatch.setattr(
        routers.profile_sources,
        "poll_device_flow",
        lambda *a, **k: (_ for _ in ()).throw(OAuthPending("authorization_pending")),
    )

    resp = client.post(
        "/api/profiles/alice/sources/ytmusic/oauth/poll", json={"device_code": "dev1"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "pending"}


# --- import (points at the same file, does not copy bytes) ---------------


def test_import_apple_music_points_at_the_exact_same_file_as_source_profile(client, config_root):
    save_profile_config(
        _profile(
            "bob",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/bob/apple_music_cookies.txt"
                )
            ),
        ),
        config_root / "profiles" / "bob.yaml",
    )

    resp = client.post(
        "/api/profiles/alice/sources/apple_music/import", json={"from_profile": "bob"}
    )

    assert resp.status_code == 200
    alice = client.get("/api/profiles/alice").json()
    bob = client.get("/api/profiles/bob").json()
    assert alice["sources"]["apple_music"]["cookies_file"] == bob["sources"]["apple_music"]["cookies_file"]
    assert alice["sources"]["apple_music"]["cookies_file"] == (
        "/config/secrets/bob/apple_music_cookies.txt"
    )


def test_import_apple_music_from_a_profile_with_no_override_points_at_global(client, config_root):
    # bob has no override -> alice should end up pointed at the shared
    # global path, same string global.yaml itself uses.
    resp = client.post(
        "/api/profiles/alice/sources/apple_music/import", json={"from_profile": "bob"}
    )

    assert resp.status_code == 200
    alice = client.get("/api/profiles/alice").json()
    assert alice["sources"]["apple_music"]["cookies_file"] == "/config/secrets/apple_music_cookies.txt"


def test_import_requires_from_profile(client):
    resp = client.post("/api/profiles/alice/sources/apple_music/import", json={})
    assert resp.status_code == 422


def test_import_rejects_unknown_source(client):
    resp = client.post(
        "/api/profiles/alice/sources/spotify/import", json={"from_profile": "bob"}
    )
    assert resp.status_code == 422


# --- revert to global -----------------------------------------------------


def test_delete_clears_override_and_reverts_to_global(client, config_root):
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/alice/apple_music_cookies.txt"
                )
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )

    resp = client.delete("/api/profiles/alice/sources/apple_music")

    assert resp.status_code == 200
    status = client.get("/api/profiles/alice/sources/status").json()
    assert status["apple_music"]["using"] == "global"
    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded.get("sources") is None


def test_delete_one_source_preserves_the_other_override(client, config_root):
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/alice/apple_music_cookies.txt"
                ),
                ytmusic=ProfileYtMusicOverride(cookies_file="/config/secrets/alice/youtube_cookies.txt"),
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )

    client.delete("/api/profiles/alice/sources/apple_music")

    reloaded = client.get("/api/profiles/alice").json()
    assert reloaded["sources"]["apple_music"] is None
    assert reloaded["sources"]["ytmusic"]["cookies_file"] == "/config/secrets/alice/youtube_cookies.txt"


def test_delete_on_profile_with_no_override_is_a_no_op(client):
    resp = client.delete("/api/profiles/alice/sources/apple_music")
    assert resp.status_code == 200
