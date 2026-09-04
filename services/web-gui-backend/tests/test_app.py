import pytest
from fastapi.testclient import TestClient

from web_gui_backend.app import create_app, create_app_from_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # create_app_from_env falls back to real defaults for anything
    # unset -- make sure a stray WEB_GUI_* var from another test/the
    # real dev environment never leaks into these.
    for var in (
        "WEB_GUI_CONFIG_ROOT",
        "WEB_GUI_SYNC_ORCHESTRATOR_DIR",
        "WEB_GUI_LIBRARY_ROOT",
        "WEB_GUI_FRONTEND_DIST",
    ):
        monkeypatch.delenv(var, raising=False)


def test_create_app_from_env_reads_config_root(tmp_path, monkeypatch):
    (tmp_path / "profiles").mkdir()
    monkeypatch.setenv("WEB_GUI_CONFIG_ROOT", str(tmp_path))

    app = create_app_from_env()
    client = TestClient(app)

    resp = client.get("/api/profiles")

    assert resp.status_code == 200


def test_create_app_from_env_reads_library_root(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    (tmp_path / "audiobooks-elsewhere" / "audiobooks" / "Some Author").mkdir(parents=True)
    monkeypatch.setenv("WEB_GUI_CONFIG_ROOT", str(config_root))
    monkeypatch.setenv("WEB_GUI_LIBRARY_ROOT", str(tmp_path / "audiobooks-elsewhere"))

    app = create_app_from_env()
    client = TestClient(app)

    resp = client.get("/api/audiobooks/browse")

    assert resp.status_code == 200
    assert resp.json()["entries"] == [{"name": "Some Author", "is_dir": True}]


def test_create_app_with_no_frontend_dist_serves_only_the_api(tmp_path):
    (tmp_path / "profiles").mkdir()
    app = create_app(config_root=tmp_path)
    client = TestClient(app)

    resp = client.get("/")

    assert resp.status_code == 404


def test_create_app_with_a_built_frontend_dist_serves_index_at_root(tmp_path):
    (tmp_path / "config" / "profiles").mkdir(parents=True)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>VCpod</title>", encoding="utf-8")

    app = create_app(config_root=tmp_path / "config", frontend_dist=dist)
    client = TestClient(app)

    resp = client.get("/")

    assert resp.status_code == 200
    assert "VCpod" in resp.text
    # API routes still take priority over the static mount
    assert client.get("/api/profiles").status_code == 200


def test_create_app_with_a_nonexistent_frontend_dist_serves_only_the_api(tmp_path):
    (tmp_path / "profiles").mkdir()
    app = create_app(config_root=tmp_path, frontend_dist=tmp_path / "no-such-dist")
    client = TestClient(app)

    assert client.get("/").status_code == 404


def test_create_app_from_env_reads_frontend_dist(tmp_path, monkeypatch):
    (tmp_path / "config" / "profiles").mkdir(parents=True)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("hello", encoding="utf-8")
    monkeypatch.setenv("WEB_GUI_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("WEB_GUI_FRONTEND_DIST", str(dist))

    app = create_app_from_env()
    client = TestClient(app)

    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.text == "hello"
