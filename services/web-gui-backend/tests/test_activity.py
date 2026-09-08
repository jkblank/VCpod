from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from common.activity import ActivityEntry, record_activity
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
from web_gui_backend.app import create_app

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _global_config() -> GlobalConfig:
    return GlobalConfig(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(enabled=True, cookies_file="/config/secrets/apple.txt"),
            spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify.json"),
            ytmusic=YtMusicSource(
                enabled=True, oauth_file="/config/secrets/oauth.json", cookies_file="/config/secrets/yt.txt"
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
        library_manager=LibraryManagerConfig(),
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    (tmp_path / "state").mkdir()
    save_global_config(_global_config(), tmp_path / "global.yaml")
    return tmp_path


@pytest.fixture
def client(config_root) -> TestClient:
    app = create_app(config_root=config_root, state_root=config_root / "state")
    return TestClient(app)


def test_activity_empty_when_nothing_recorded(client):
    resp = client.get("/api/activity")

    assert resp.status_code == 200
    assert resp.json() == {"entries": []}


def test_activity_returns_recorded_entries_newest_first(client, config_root):
    state_root = config_root / "state"
    record_activity(
        state_root,
        ActivityEntry(
            started_at=NOW - timedelta(hours=1),
            service="fetch-scheduler",
            profile="alice",
            description="older",
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
            description="newer",
            duration_seconds=2.0,
            result="ok",
        ),
    )

    resp = client.get("/api/activity")

    entries = resp.json()["entries"]
    assert [e["description"] for e in entries] == ["newer", "older"]
    assert entries[0]["service"] == "sync-orchestrator"
    assert entries[0]["result"] == "ok"


def test_activity_respects_limit_query_param(client, config_root):
    state_root = config_root / "state"
    for i in range(5):
        record_activity(
            state_root,
            ActivityEntry(
                started_at=NOW - timedelta(hours=i),
                service="fetch-scheduler",
                profile="alice",
                description=f"n{i}",
                duration_seconds=1.0,
                result="ok",
            ),
        )

    resp = client.get("/api/activity", params={"limit": 2})

    assert len(resp.json()["entries"]) == 2
