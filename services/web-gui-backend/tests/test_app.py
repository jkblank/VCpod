import pytest
from fastapi.testclient import TestClient

from web_gui_backend.app import create_app_from_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # create_app_from_env falls back to real defaults for anything
    # unset -- make sure a stray WEB_GUI_* var from another test/the
    # real dev environment never leaks into these.
    for var in ("WEB_GUI_CONFIG_ROOT", "WEB_GUI_SYNC_ORCHESTRATOR_DIR", "WEB_GUI_LIBRARY_ROOT"):
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
