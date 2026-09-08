from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from common.activity import ActivityEntry, record_activity, record_last_sync
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
from common.state import StateDB, TrackRecord

from web_gui_backend.app import create_app

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


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
    from web_gui_backend.routers import overview as overview_module

    monkeypatch.setattr(
        overview_module, "identify_connected_devices", lambda *a, **k: list(connected_devices)
    )
    app = create_app(
        config_root=config_root,
        library_root=config_root / "library",
        state_root=config_root / "state",
    )
    return TestClient(app)


def test_overview_empty_workspace(config_root, monkeypatch):
    client = _client(config_root, monkeypatch)

    resp = client.get("/api/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["devices"] == []
    assert body["library"] == {"track_count": 0}
    assert body["recent_activity"] == []


def test_overview_device_card_not_connected(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")

    body = _client(config_root, monkeypatch).get("/api/overview").json()

    card = body["devices"][0]
    assert card["profile"] == "alice"
    assert card["connected_device"] is None
    assert card["track_count"] == 0
    assert card["last_sync"] is None


def test_overview_device_card_connected_and_matched_by_volume_label(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    connected = {
        "path": "/mnt/ipod", "volume_label": "VOL-alice", "serial": "SN1", "firewire_guid": "FW1",
        "model_family": "iPod", "generation": "5.5th Gen", "model_number": "MA450",
        "capacity": "80GB", "used_bytes": 100, "free_bytes": 900,
    }

    client = _client(config_root, monkeypatch, connected_devices=[connected])
    body = client.get("/api/overview").json()

    card = body["devices"][0]
    assert card["connected_device"] == connected


def test_overview_reports_real_track_and_episode_counts_and_last_sync(config_root, monkeypatch):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    state_root = config_root / "state"
    with StateDB(state_root / "alice.sqlite") as db:
        db.record_track(
            TrackRecord(
                source="apple_music", source_id="1", local_path="/library/music/a.m4a",
                title="A", artist="B", downloaded_at=NOW.isoformat(),
            )
        )
    record_last_sync(state_root, "alice", NOW)

    body = _client(config_root, monkeypatch).get("/api/overview").json()

    card = body["devices"][0]
    assert card["track_count"] == 1
    assert card["last_sync"] == NOW.isoformat()


def test_overview_library_track_count_reflects_real_files(config_root, monkeypatch):
    music_root = config_root / "library" / "music"
    (music_root / "Artist" / "Album").mkdir(parents=True)
    (music_root / "Artist" / "Album" / "track.m4a").write_bytes(b"data")

    body = _client(config_root, monkeypatch).get("/api/overview").json()

    assert body["library"]["track_count"] == 1


def test_overview_recent_activity_newest_first_limited_to_five(config_root, monkeypatch):
    state_root = config_root / "state"
    for i in range(7):
        record_activity(
            state_root,
            ActivityEntry(
                started_at=NOW,
                service="fetch-scheduler",
                profile="alice",
                description=f"n{i}",
                duration_seconds=1.0,
                result="ok",
            ),
        )

    body = _client(config_root, monkeypatch).get("/api/overview").json()

    assert len(body["recent_activity"]) == 5


def test_overview_includes_alerts(config_root, monkeypatch):
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

    body = _client(config_root, monkeypatch).get("/api/overview").json()

    assert any(a["kind"] == "Apple Music cookies" for a in body["alerts"])
