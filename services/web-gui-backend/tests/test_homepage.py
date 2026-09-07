import fcntl
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from common.activity import ActivityEntry, record_activity
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

NOW = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)


def _global_config(**overrides) -> GlobalConfig:
    base = dict(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(enabled=False, cookies_file="/config/secrets/apple.txt"),
            spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify.json"),
            ytmusic=YtMusicSource(
                enabled=False, oauth_file="/config/secrets/oauth.json", cookies_file="/config/secrets/yt.txt"
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
        library_manager=LibraryManagerConfig(),
    )
    base.update(overrides)
    return GlobalConfig(**base)


def _profile(name: str, **overrides) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="volume_label", match_value=f"VOL-{name}"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file=f"/config/secrets/pc/{name}.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
        **overrides,
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "state").mkdir()
    (tmp_path / "library" / "music").mkdir(parents=True)
    save_global_config(_global_config(), tmp_path / "global.yaml")
    return tmp_path


def _client(config_root, monkeypatch, connected_devices=()) -> TestClient:
    from web_gui_backend.routers import homepage as homepage_module

    monkeypatch.setattr(
        homepage_module, "identify_connected_devices", lambda *a, **k: list(connected_devices)
    )
    app = create_app(
        config_root=config_root,
        library_root=config_root / "library",
        state_root=config_root / "state",
    )
    return TestClient(app)


def test_homepage_status_empty_workspace_is_ok(config_root, monkeypatch):
    body = _client(config_root, monkeypatch).get("/api/homepage/status").json()

    assert body == {
        "status": "ok",
        "sync_running": False,
        "profiles_count": 0,
        "devices_connected": 0,
        "alerts_count": 0,
        "last_sync_at": None,
        "last_sync_profile": None,
        "last_sync_result": None,
        "last_sync_description": None,
    }


def test_homepage_status_counts_profiles_and_connected_devices(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    save_profile_config(_profile("bob"), config_root / "profiles" / "bob.yaml")

    body = _client(
        config_root, monkeypatch, connected_devices=[{"volume_label": "VOL-alice", "serial": "SN1"}]
    ).get("/api/homepage/status").json()

    assert body["profiles_count"] == 2
    assert body["devices_connected"] == 1


def test_homepage_status_reports_alerts_and_attention_status(config_root, monkeypatch):
    save_global_config(
        _global_config(
            sources=SourcesConfig(
                apple_music=AppleMusicSource(enabled=True, cookies_file="/config/secrets/apple.txt"),
                spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify.json"),
                ytmusic=YtMusicSource(
                    enabled=False, oauth_file="/config/secrets/oauth.json", cookies_file="/config/secrets/yt.txt"
                ),
            )
        ),
        config_root / "global.yaml",
    )

    body = _client(config_root, monkeypatch).get("/api/homepage/status").json()

    assert body["alerts_count"] >= 1
    assert body["status"] == "attention"


def test_homepage_status_last_sync_reflects_most_recent_sync_orchestrator_entry(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    state_root = config_root / "state"
    record_activity(
        state_root,
        ActivityEntry(
            started_at=NOW,
            service="fetch-scheduler",
            profile="alice",
            description="fetched 3 tracks",
            duration_seconds=1.0,
            result="ok",
        ),
    )
    record_activity(
        state_root,
        ActivityEntry(
            started_at=NOW,
            service="sync-orchestrator",
            profile="alice",
            description="sync — execute failed: boom",
            duration_seconds=2.0,
            result="error",
        ),
    )

    body = _client(config_root, monkeypatch).get("/api/homepage/status").json()

    assert body["last_sync_profile"] == "alice"
    assert body["last_sync_result"] == "error"
    assert body["last_sync_description"] == "sync — execute failed: boom"
    assert body["status"] == "error"


def test_homepage_status_sync_running_takes_priority_over_error(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    state_root = config_root / "state"
    record_activity(
        state_root,
        ActivityEntry(
            started_at=NOW,
            service="sync-orchestrator",
            profile="alice",
            description="sync — execute failed: boom",
            duration_seconds=2.0,
            result="error",
        ),
    )
    lock_path = state_root / ".sync_alice.lock"
    fd = open(lock_path, "a")
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        body = _client(config_root, monkeypatch).get("/api/homepage/status").json()
        assert body["sync_running"] is True
        assert body["status"] == "syncing"
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
