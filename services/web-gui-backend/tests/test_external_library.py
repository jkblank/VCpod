import pytest
from fastapi.testclient import TestClient

from web_gui_backend.app import create_app


@pytest.fixture
def library_tree(tmp_path):
    root = tmp_path / "personal-library"
    (root / "Linkin Park" / "Meteora").mkdir(parents=True)
    return root


@pytest.fixture
def client(tmp_path) -> TestClient:
    (tmp_path / "profiles").mkdir()
    app = create_app(config_root=tmp_path)
    return TestClient(app)


def test_browse_lists_root(client, library_tree):
    resp = client.get("/api/external-library/browse", params={"root": str(library_tree)})

    assert resp.status_code == 200
    body = resp.json()
    assert body["subpath"] == ""
    assert body["entries"] == [{"name": "Linkin Park", "is_dir": True}]


def test_browse_descends_into_subpath(client, library_tree):
    resp = client.get(
        "/api/external-library/browse",
        params={"root": str(library_tree), "subpath": "Linkin Park"},
    )

    assert resp.status_code == 200
    assert resp.json()["entries"] == [{"name": "Meteora", "is_dir": True}]


def test_browse_rejects_nonexistent_root(client, tmp_path):
    resp = client.get(
        "/api/external-library/browse", params={"root": str(tmp_path / "nope")}
    )

    assert resp.status_code == 422


def test_browse_rejects_path_escape(client, library_tree):
    resp = client.get(
        "/api/external-library/browse",
        params={"root": str(library_tree), "subpath": "../../etc"},
    )

    assert resp.status_code == 422
    assert "escapes" in resp.json()["detail"]
