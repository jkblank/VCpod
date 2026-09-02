from pathlib import Path

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
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SourcesConfig,
    SpotifySource,
    SyncSettings,
    YtMusicSource,
)
from web_gui_backend.app import create_app


def _profile(name: str) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="serial", match_value="ABC123"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file=f"/config/secrets/pocketcasts/{name}.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )


def _global_config() -> GlobalConfig:
    return GlobalConfig(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(enabled=True, cookies_file="/config/secrets/apple_music_cookies.txt"),
            spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify_credentials.json"),
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
def client(tmp_path) -> TestClient:
    (tmp_path / "profiles").mkdir()
    app = create_app(config_root=tmp_path)
    return TestClient(app)


@pytest.fixture
def config_root(tmp_path) -> Path:
    return tmp_path


def test_list_profiles_empty_directory(client):
    resp = client.get("/api/profiles")
    assert resp.status_code == 200
    assert resp.json() == {}


def test_list_profiles_returns_seeded_profiles(client, config_root):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    save_profile_config(_profile("bob"), config_root / "profiles" / "bob.yaml")

    resp = client.get("/api/profiles")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"alice", "bob"}
    assert body["alice"]["device"]["match_value"] == "ABC123"


def test_get_profile_returns_404_for_missing_profile(client):
    resp = client.get("/api/profiles/nobody")
    assert resp.status_code == 404


def test_get_profile_returns_full_profile(client, config_root):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")

    resp = client.get("/api/profiles/alice")

    assert resp.status_code == 200
    assert resp.json()["profile"] == "alice"


def test_put_profile_creates_new_profile(client, config_root):
    body = _profile("newperson").model_dump(mode="json")

    resp = client.put("/api/profiles/newperson", json=body)

    assert resp.status_code == 200
    assert resp.json()["profile"] == "newperson"
    assert (config_root / "profiles" / "newperson.yaml").is_file()


def test_put_profile_updates_existing_profile(client, config_root):
    path = config_root / "profiles" / "alice.yaml"
    save_profile_config(_profile("alice"), path)
    body = _profile("alice").model_dump(mode="json")
    body["device"]["match_value"] = "CHANGED"

    resp = client.put("/api/profiles/alice", json=body)

    assert resp.status_code == 200
    assert resp.json()["device"]["match_value"] == "CHANGED"


def test_put_profile_rejects_name_mismatch(client):
    body = _profile("alice").model_dump(mode="json")

    resp = client.put("/api/profiles/bob", json=body)

    assert resp.status_code == 400


def test_put_profile_rejects_reserved_name(client):
    body = _profile("global").model_dump(mode="json")

    resp = client.put("/api/profiles/global", json=body)

    assert resp.status_code == 422
    assert "reserved" in str(resp.json()["detail"])


def test_put_profile_rejects_duplicate_name(client, config_root):
    # Existing file first.yaml already claims profile "dupe" -- PUTting a
    # *different* file (config_root/profiles/dupe.yaml, matching the URL
    # name/body.profile pair, same as any other creation) for the same
    # profile name must be rejected as a duplicate, not silently allowed
    # just because the target filename happens to differ from first.yaml.
    save_profile_config(_profile("dupe"), config_root / "profiles" / "first.yaml")
    body = _profile("dupe").model_dump(mode="json")

    resp = client.put("/api/profiles/dupe", json=body)

    assert resp.status_code == 422
    assert "duplicate profile name" in str(resp.json()["detail"])


def test_put_profile_rejects_invalid_field_with_path_and_message(client):
    body = _profile("badenum").model_dump(mode="json")
    body["device"]["match_by"] = "not-a-real-value"

    resp = client.put("/api/profiles/badenum", json=body)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("device.match_by" in e for e in detail["errors"])


def test_delete_profile_removes_the_file(client, config_root):
    path = config_root / "profiles" / "alice.yaml"
    save_profile_config(_profile("alice"), path)

    resp = client.delete("/api/profiles/alice")

    assert resp.status_code == 204
    assert not path.is_file()


def test_delete_profile_returns_404_for_missing_profile(client):
    resp = client.delete("/api/profiles/nobody")
    assert resp.status_code == 404


def test_get_global_config_returns_saved_config(client, config_root):
    save_global_config(_global_config(), config_root / "global.yaml")

    resp = client.get("/api/global-config")

    assert resp.status_code == 200
    assert resp.json()["sources"]["apple_music"]["enabled"] is True


def test_put_global_config_round_trips(client, config_root):
    body = _global_config().model_dump(mode="json")
    body["sources"]["spotify"]["enabled"] = True

    resp = client.put("/api/global-config", json=body)

    assert resp.status_code == 200
    assert resp.json()["sources"]["spotify"]["enabled"] is True
    assert load_global_config_enabled_spotify(config_root)


def load_global_config_enabled_spotify(config_root: Path) -> bool:
    from common.config import load_global_config

    return load_global_config(config_root / "global.yaml").sources.spotify.enabled
