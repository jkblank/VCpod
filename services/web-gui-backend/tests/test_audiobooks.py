import pytest
from fastapi.testclient import TestClient

from web_gui_backend.app import create_app


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    return tmp_path


@pytest.fixture
def library_root(tmp_path):
    root = tmp_path / "library"
    (root / "audiobooks" / "Franz Kafka" / "The Trial").mkdir(parents=True)
    return root


@pytest.fixture
def client(config_root, library_root) -> TestClient:
    app = create_app(config_root=config_root, library_root=library_root)
    return TestClient(app)


def test_browse_lists_authors(client):
    resp = client.get("/api/audiobooks/browse")

    assert resp.status_code == 200
    assert resp.json() == {"subpath": "", "entries": [{"name": "Franz Kafka", "is_dir": True}]}


def test_browse_descends_into_author(client):
    resp = client.get("/api/audiobooks/browse", params={"subpath": "Franz Kafka"})

    assert resp.status_code == 200
    assert resp.json()["entries"] == [{"name": "The Trial", "is_dir": True}]


def test_browse_rejects_missing_audiobooks_dir(tmp_path):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    app = create_app(config_root=config_root, library_root=tmp_path / "library-with-no-audiobooks")
    client = TestClient(app)

    resp = client.get("/api/audiobooks/browse")

    assert resp.status_code == 422


def test_browse_defaults_library_root_to_sibling_of_config_root(tmp_path):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    (tmp_path / "library" / "audiobooks" / "Some Author").mkdir(parents=True)
    app = create_app(config_root=config_root)  # no explicit library_root
    client = TestClient(app)

    resp = client.get("/api/audiobooks/browse")

    assert resp.status_code == 200
    assert resp.json()["entries"] == [{"name": "Some Author", "is_dir": True}]
