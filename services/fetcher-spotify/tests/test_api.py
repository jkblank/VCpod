import pytest

from fetcher_spotify import api as api_module
from fetcher_spotify.api import get_playlist_tracks, list_playlists


class FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._data


class FakeClient:
    def __init__(self, responses: dict[str, dict]):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def get(self, url: str, headers: dict) -> FakeResponse:
        assert headers["Authorization"] == "Bearer fake-token"
        return FakeResponse(self._responses[url])


@pytest.fixture(autouse=True)
def patch_token(monkeypatch):
    monkeypatch.setattr(api_module, "_get_access_token", lambda credentials_path: "fake-token")


def _patch_client(monkeypatch, responses: dict[str, dict]):
    monkeypatch.setattr(api_module.httpx, "Client", lambda: FakeClient(responses))


def _playlist_item(playlist_id: str, name: str, track_count: int = 0, owner: str | None = None):
    return {
        "id": playlist_id,
        "name": name,
        "owner": {"display_name": owner} if owner else {"display_name": None},
        "tracks": {"total": track_count},
    }


def test_list_playlists_single_page(monkeypatch):
    url = f"{api_module.ME_PLAYLISTS_URL}?limit=50"
    responses = {
        url: {
            "items": [
                _playlist_item("p1", "Songs to vape to", 12, None),
                _playlist_item("p2", "Zanny twitch playlist", 34, "friend123"),
            ],
            "next": None,
        }
    }
    _patch_client(monkeypatch, responses)

    result = list_playlists("creds.json")

    assert [p.source_id for p in result] == ["p1", "p2"]
    assert result[0].track_count == 12
    assert result[0].owner is None
    assert result[1].owner == "friend123"


def test_list_playlists_follows_next_url(monkeypatch):
    url1 = f"{api_module.ME_PLAYLISTS_URL}?limit=50"
    url2 = f"{api_module.ME_PLAYLISTS_URL}?limit=50&offset=50"
    responses = {
        url1: {"items": [_playlist_item("p1", "First")], "next": url2},
        url2: {"items": [_playlist_item("p2", "Second")], "next": None},
    }
    _patch_client(monkeypatch, responses)

    result = list_playlists("creds.json")

    assert [p.source_id for p in result] == ["p1", "p2"]


def test_get_playlist_tracks_parses_and_skips_null_tracks(monkeypatch):
    url = f"{api_module.PLAYLISTS_URL}/p1/tracks?limit=100"
    responses = {
        url: {
            "items": [
                {
                    "track": {
                        "id": "t1",
                        "name": "Song One",
                        "artists": [{"name": "Artist One"}],
                        "album": {"name": "Album One"},
                        "track_number": 3,
                        "external_ids": {"isrc": "USRC12345678"},
                    }
                },
                {"track": None},
                {
                    "track": {
                        "id": "t2",
                        "name": "Song Two",
                        "artists": [{"name": "Artist Two"}],
                        "album": {"name": "Album Two"},
                        "track_number": 1,
                        # no external_ids at all — some tracks lack ISRC
                    }
                },
            ],
            "next": None,
        }
    }
    _patch_client(monkeypatch, responses)

    result = get_playlist_tracks("creds.json", "p1")

    assert [t.source_id for t in result] == ["t1", "t2"]
    assert result[0].title == "Song One"
    assert result[0].artist == "Artist One"
    assert result[0].album == "Album One"
    assert result[0].track_number == 3
    assert result[0].isrc == "USRC12345678"
    assert result[1].isrc is None
