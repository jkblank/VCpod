from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from audiobook_manager.discover import record_import
from audiobook_manager.pipeline import ImportOutcome, ImportPipelineError
from common.config import save_global_config
from common.models import (
    AppleMusicSource,
    AudiobookManagerConfig,
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


def _global_config(discover_root: str = "") -> GlobalConfig:
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
        audiobook_manager=AudiobookManagerConfig(discover_root=discover_root),
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    return tmp_path


@pytest.fixture
def state_root(tmp_path):
    return tmp_path / "state"


def _client(config_root, state_root, discover_root: str = "") -> TestClient:
    save_global_config(_global_config(discover_root), config_root / "global.yaml")
    app = create_app(config_root=config_root, state_root=state_root)
    return TestClient(app)


def test_discover_returns_empty_when_no_root_configured(config_root, state_root):
    client = _client(config_root, state_root, discover_root="")

    resp = client.get("/api/audiobooks/discover")

    assert resp.status_code == 200
    assert resp.json() == {"root": "", "books": []}


def test_discover_lists_real_folders_under_the_configured_root(config_root, state_root, tmp_path):
    drop_zone = tmp_path / "drop-zone"
    (drop_zone / "Franz Kafka - The Trial").mkdir(parents=True)
    (drop_zone / "Franz Kafka - The Trial" / "01.mp3").write_bytes(b"")
    client = _client(config_root, state_root, discover_root=str(drop_zone))

    resp = client.get("/api/audiobooks/discover")

    assert resp.status_code == 200
    body = resp.json()
    assert body["root"] == str(drop_zone)
    assert len(body["books"]) == 1
    book = body["books"][0]
    assert book["name"] == "Franz Kafka - The Trial"
    assert book["audio_file_count"] == 1
    assert book["already_imported"] is False


def test_discover_flags_already_imported_books(config_root, state_root, tmp_path):
    drop_zone = tmp_path / "drop-zone"
    (drop_zone / "Franz Kafka - The Trial").mkdir(parents=True)
    (drop_zone / "Franz Kafka - The Trial" / "01.mp3").write_bytes(b"")
    record_import(state_root, "Franz Kafka - The Trial", [Path("/lib/trial.m4b")])
    client = _client(config_root, state_root, discover_root=str(drop_zone))

    resp = client.get("/api/audiobooks/discover")

    book = resp.json()["books"][0]
    assert book["already_imported"] is True
    assert book["library_paths"] == ["/lib/trial.m4b"]


def test_import_discovered_audiobook_requires_name(config_root, state_root):
    client = _client(config_root, state_root, discover_root="/somewhere")

    resp = client.post("/api/audiobooks/discover/import", json={})

    assert resp.status_code == 422
    assert "name is required" in resp.json()["detail"]


def test_import_discovered_audiobook_requires_discover_root_configured(config_root, state_root):
    client = _client(config_root, state_root, discover_root="")

    resp = client.post("/api/audiobooks/discover/import", json={"name": "Some Book"})

    assert resp.status_code == 422
    assert "no discover_root configured" in resp.json()["detail"]


def test_import_discovered_audiobook_returns_502_on_pipeline_error(
    monkeypatch, config_root, state_root
):
    def _boom(parts_dir, *, library_root, state_root):
        raise ImportPipelineError("no ffmpeg on PATH")

    monkeypatch.setattr(routers.audiobooks_discover, "run_import_audiobook", _boom)
    client = _client(config_root, state_root, discover_root="/drop-zone")

    resp = client.post("/api/audiobooks/discover/import", json={"name": "Some Book"})

    assert resp.status_code == 502
    assert "no ffmpeg" in resp.json()["detail"]


def test_import_discovered_audiobook_returns_422_when_beets_could_not_match(
    monkeypatch, config_root, state_root
):
    staging_dir = state_root / "audiobooks" / "staging" / "Some Book"

    def _skip(parts_dir, *, library_root, state_root):
        return ImportOutcome(imported=False, imported_paths=[], staging_dir=staging_dir)

    monkeypatch.setattr(routers.audiobooks_discover, "run_import_audiobook", _skip)
    client = _client(config_root, state_root, discover_root="/drop-zone")

    resp = client.post("/api/audiobooks/discover/import", json={"name": "Some Book"})

    assert resp.status_code == 422
    assert "could not confidently match" in resp.json()["detail"]
    assert str(staging_dir) in resp.json()["detail"]


def test_import_discovered_audiobook_success(monkeypatch, config_root, state_root):
    captured = {}
    imported_path = Path("/lib/Franz Kafka/The Trial.m4b")

    def _capture(parts_dir, *, library_root, state_root):
        captured["parts_dir"] = parts_dir
        captured["library_root"] = library_root
        return ImportOutcome(
            imported=True, imported_paths=[imported_path], staging_dir=Path("/staging")
        )

    monkeypatch.setattr(routers.audiobooks_discover, "run_import_audiobook", _capture)
    client = _client(config_root, state_root, discover_root="/drop-zone")

    resp = client.post(
        "/api/audiobooks/discover/import", json={"name": "Franz Kafka - The Trial"}
    )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "imported_paths": [str(imported_path)]}
    assert captured["parts_dir"] == "/drop-zone/Franz Kafka - The Trial"
