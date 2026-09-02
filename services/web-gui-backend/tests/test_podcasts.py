import json
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from common.config import save_profile_config
from common.models import (
    DeviceMatch,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SyncSettings,
)
from web_gui_backend import routers
from web_gui_backend.app import create_app


@dataclass
class _FakeSubscription:
    uuid: str
    title: str
    author: str


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


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    save_profile_config(_profile("alice"), tmp_path / "profiles" / "alice.yaml")
    return tmp_path


@pytest.fixture
def client(config_root) -> TestClient:
    app = create_app(config_root=config_root)
    return TestClient(app)


def test_subscriptions_returns_502_when_no_credentials_saved_yet(client):
    resp = client.get("/api/profiles/alice/pocketcasts/subscriptions")

    assert resp.status_code == 502
    assert "not saved yet" in resp.json()["detail"]


def test_subscriptions_returns_404_for_unknown_profile(client):
    resp = client.get("/api/profiles/nobody/pocketcasts/subscriptions")
    assert resp.status_code == 404


def test_subscriptions_returns_listed_shows_once_credentials_exist(monkeypatch, client, config_root):
    creds_path = config_root / "secrets" / "pocketcasts" / "alice.json"
    creds_path.parent.mkdir(parents=True)
    creds_path.write_text(json.dumps({"email": "alice@example.com", "password": "hunter2"}))

    monkeypatch.setattr(routers.podcasts, "login", lambda email, password: "token123")
    monkeypatch.setattr(
        routers.podcasts,
        "list_subscriptions",
        lambda token: [_FakeSubscription("uuid-1", "Search Engine", "Vox")],
    )

    resp = client.get("/api/profiles/alice/pocketcasts/subscriptions")

    assert resp.status_code == 200
    assert resp.json() == [{"uuid": "uuid-1", "title": "Search Engine", "author": "Vox"}]


def test_subscriptions_returns_502_when_login_fails(monkeypatch, client, config_root):
    creds_path = config_root / "secrets" / "pocketcasts" / "alice.json"
    creds_path.parent.mkdir(parents=True)
    creds_path.write_text(json.dumps({"email": "alice@example.com", "password": "wrong"}))

    def _boom(email, password):
        raise ValueError("401 Unauthorized")

    monkeypatch.setattr(routers.podcasts, "login", _boom)

    resp = client.get("/api/profiles/alice/pocketcasts/subscriptions")

    assert resp.status_code == 502


def test_put_credentials_rejects_missing_fields(client):
    resp = client.put("/api/profiles/alice/pocketcasts-credentials", json={"email": "a@b.com"})
    assert resp.status_code == 422


def test_put_credentials_validates_via_real_login_before_saving(monkeypatch, client, config_root):
    def _boom(email, password):
        raise ValueError("401 Unauthorized")

    monkeypatch.setattr(routers.podcasts, "login", _boom)

    resp = client.put(
        "/api/profiles/alice/pocketcasts-credentials",
        json={"email": "alice@example.com", "password": "wrong"},
    )

    assert resp.status_code == 422
    assert "rejected" in resp.json()["detail"]
    assert not (config_root / "secrets" / "pocketcasts" / "alice.json").exists()


def test_put_credentials_saves_on_successful_login(monkeypatch, client, config_root):
    monkeypatch.setattr(routers.podcasts, "login", lambda email, password: "token123")

    resp = client.put(
        "/api/profiles/alice/pocketcasts-credentials",
        json={"email": "alice@example.com", "password": "correct-horse"},
    )

    assert resp.status_code == 200
    saved = json.loads((config_root / "secrets" / "pocketcasts" / "alice.json").read_text())
    assert saved == {"email": "alice@example.com", "password": "correct-horse"}


def test_put_credentials_returns_404_for_unknown_profile(monkeypatch, client):
    monkeypatch.setattr(routers.podcasts, "login", lambda email, password: "token123")

    resp = client.put(
        "/api/profiles/nobody/pocketcasts-credentials",
        json={"email": "a@b.com", "password": "x"},
    )

    assert resp.status_code == 404
