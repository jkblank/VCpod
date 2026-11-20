import json
from dataclasses import dataclass
from pathlib import Path

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
from common.state import EpisodeRecord, StateDB
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


def test_pocketcasts_status_reports_missing_credentials(client):
    resp = client.get("/api/profiles/alice/pocketcasts-status")

    assert resp.status_code == 200
    assert resp.json() == {"exists": False, "updated_at": None}


def test_pocketcasts_status_reports_existing_credentials(client, config_root):
    creds_path = config_root / "secrets" / "pocketcasts" / "alice.json"
    creds_path.parent.mkdir(parents=True)
    creds_path.write_text(json.dumps({"email": "alice@example.com", "password": "x"}))

    resp = client.get("/api/profiles/alice/pocketcasts-status")

    assert resp.status_code == 200
    body = resp.json()
    assert body["exists"] is True
    assert isinstance(body["updated_at"], float)


def test_pocketcasts_status_returns_404_for_unknown_profile(client):
    resp = client.get("/api/profiles/nobody/pocketcasts-status")
    assert resp.status_code == 404


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


# --- Search + single-episode download for shows NOT in the account's
# subscriptions. Uses its own fixture (explicit library_root/state_root
# under tmp_path) rather than the `client` fixture above -- `client`'s
# config_root fixture leaves library_root/state_root on create_app's
# default (a sibling of config_root.parent), which is fine for the
# credentials-only tests above but not hermetic enough once a test
# needs to assert exactly where a file landed.


@pytest.fixture
def client_with_roots(tmp_path) -> tuple[TestClient, Path, Path]:
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    library_root = tmp_path / "library"
    state_root = tmp_path / "state"
    app = create_app(config_root=config_root, library_root=library_root, state_root=state_root)
    return TestClient(app), library_root, state_root


def test_podcasts_search_returns_results(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    @dataclass
    class _FakeResult:
        collection_id: int
        title: str
        author: str
        feed_url: str
        artwork_url: str | None

    monkeypatch.setattr(
        routers.podcasts,
        "search_podcasts",
        lambda term: [_FakeResult(1, "Linux Lads", "Linux Lads", "https://e.com/f.xml", None)],
    )

    resp = client.get("/api/podcasts/search", params={"q": "linux"})

    assert resp.status_code == 200
    assert resp.json() == [
        {
            "collection_id": 1,
            "title": "Linux Lads",
            "author": "Linux Lads",
            "feed_url": "https://e.com/f.xml",
            "artwork_url": None,
        }
    ]


def test_podcasts_search_returns_empty_for_blank_query_without_calling_out(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    def _fail(term):
        raise AssertionError("must not search for a blank query")

    monkeypatch.setattr(routers.podcasts, "search_podcasts", _fail)

    resp = client.get("/api/podcasts/search", params={"q": "   "})

    assert resp.status_code == 200
    assert resp.json() == []


def test_podcasts_search_returns_502_on_failure(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    def _boom(term):
        raise ValueError("iTunes Search API is down")

    monkeypatch.setattr(routers.podcasts, "search_podcasts", _boom)

    resp = client.get("/api/podcasts/search", params={"q": "linux"})

    assert resp.status_code == 502


def test_podcasts_episodes_returns_parsed_list(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    @dataclass
    class _FakeEpisode:
        enclosure_url: str
        title: str
        description: str
        episode_number: int | None
        season_number: int | None
        published: str | None
        duration_seconds: int | None

    monkeypatch.setattr(
        routers.podcasts,
        "fetch_rss_episodes",
        lambda feed_url: [
            _FakeEpisode("https://e.com/ep1.mp3", "Ep 1", "desc", None, None, "2026-09-01", 120)
        ],
    )

    resp = client.get("/api/podcasts/episodes", params={"feed_url": "https://e.com/f.xml"})

    assert resp.status_code == 200
    assert resp.json()[0]["title"] == "Ep 1"
    assert resp.json()[0]["duration_seconds"] == 120


def test_post_one_off_download_returns_404_for_unknown_profile(client_with_roots):
    client, _, _ = client_with_roots

    resp = client.post(
        "/api/profiles/nobody/podcasts/one-off",
        json={
            "show_title": "Show",
            "feed_url": "https://e.com/f.xml",
            "episode": {"enclosure_url": "https://e.com/ep.mp3", "title": "Ep"},
        },
    )

    assert resp.status_code == 404


def test_post_one_off_download_rejects_missing_fields(client_with_roots):
    client, _, _ = client_with_roots

    resp = client.post(
        "/api/profiles/alice/podcasts/one-off",
        json={"show_title": "Show", "feed_url": "https://e.com/f.xml", "episode": {}},
    )

    assert resp.status_code == 422


def test_post_one_off_download_happy_path_uses_podcasts_subdir_of_library_root(
    monkeypatch, client_with_roots
):
    client, library_root, state_root = client_with_roots
    captured = {}

    def _fake_download(*, show_title, feed_url, episode, library_root, state_db_path, **kwargs):
        captured["show_title"] = show_title
        captured["feed_url"] = feed_url
        captured["library_root"] = library_root
        captured["state_db_path"] = state_db_path
        return EpisodeRecord(
            episode_uuid="oneoff:abc",
            podcast_uuid="oneoff:abc",
            show_name=show_title,
            local_path=str(library_root / show_title / "ep.mp3"),
            played=False,
            played_up_to=0,
            downloaded_at="2026-09-01T00:00:00+00:00",
            one_off=True,
        )

    monkeypatch.setattr(routers.podcasts, "download_one_off_episode", _fake_download)

    resp = client.post(
        "/api/profiles/alice/podcasts/one-off",
        json={
            "show_title": "Standalone Show",
            "feed_url": "https://e.com/f.xml",
            "episode": {
                "enclosure_url": "https://e.com/ep.mp3",
                "title": "Standalone Episode",
                "duration_seconds": 120,
            },
        },
    )

    assert resp.status_code == 200
    assert resp.json()["one_off"] is True
    assert resp.json()["episode_uuid"] == "oneoff:abc"
    assert captured["library_root"] == library_root / "podcasts"
    assert captured["state_db_path"] == state_root / "alice.sqlite"


def test_post_one_off_download_returns_502_on_failure(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    def _boom(**kwargs):
        raise RuntimeError("download failed")

    monkeypatch.setattr(routers.podcasts, "download_one_off_episode", _boom)

    resp = client.post(
        "/api/profiles/alice/podcasts/one-off",
        json={
            "show_title": "Show",
            "feed_url": "https://e.com/f.xml",
            "episode": {"enclosure_url": "https://e.com/ep.mp3", "title": "Ep"},
        },
    )

    assert resp.status_code == 502


def test_get_one_off_episodes_lists_only_one_off_rows(client_with_roots):
    client, _, state_root = client_with_roots
    with StateDB(state_root / "alice.sqlite") as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-sub",
                podcast_uuid="show-1",
                show_name="Subscribed Show",
                local_path="/data/library/podcasts/Subscribed Show/ep.mp3",
                played=False,
                played_up_to=0,
                downloaded_at="2026-09-01T00:00:00+00:00",
            )
        )
        db.record_one_off_episode(
            EpisodeRecord(
                episode_uuid="oneoff:abc",
                podcast_uuid="oneoff:abc",
                show_name="Standalone Show",
                local_path="/data/library/podcasts/Standalone Show/ep.mp3",
                played=False,
                played_up_to=0,
                downloaded_at="2026-09-01T00:00:00+00:00",
            )
        )

    resp = client.get("/api/profiles/alice/podcasts/one-off")

    assert resp.status_code == 200
    body = resp.json()
    assert [e["episode_uuid"] for e in body] == ["oneoff:abc"]


def test_get_one_off_episodes_returns_404_for_unknown_profile(client_with_roots):
    client, _, _ = client_with_roots

    resp = client.get("/api/profiles/nobody/podcasts/one-off")

    assert resp.status_code == 404


def test_delete_one_off_returns_ok_on_success(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    monkeypatch.setattr(
        routers.podcasts,
        "delete_one_off_episode",
        lambda episode_uuid, *, state_db_path: EpisodeRecord(
            episode_uuid=episode_uuid,
            podcast_uuid=episode_uuid,
            show_name="Standalone Show",
            local_path="/data/x.mp3",
            played=False,
            played_up_to=0,
            downloaded_at="2026-09-01T00:00:00+00:00",
            one_off=True,
        ),
    )

    resp = client.delete("/api/profiles/alice/podcasts/one-off/oneoff:abc")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_delete_one_off_returns_404_when_not_found(monkeypatch, client_with_roots):
    client, _, _ = client_with_roots

    monkeypatch.setattr(
        routers.podcasts, "delete_one_off_episode", lambda episode_uuid, *, state_db_path: None
    )

    resp = client.delete("/api/profiles/alice/podcasts/one-off/does-not-exist")

    assert resp.status_code == 404


def test_delete_one_off_returns_404_for_unknown_profile(client_with_roots):
    client, _, _ = client_with_roots

    resp = client.delete("/api/profiles/nobody/podcasts/one-off/oneoff:abc")

    assert resp.status_code == 404
