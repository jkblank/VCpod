import pytest
from gamdl.api import AppleMusicApi

from fetcher_apple.api import get_playlist_tracks, list_playlists


def _playlist_item(
    global_id: str,
    name: str,
    track_count: int = 0,
    curator: str | None = None,
    library_id: str | None = None,
):
    library_id = library_id or f"p.internal-{global_id}"
    attrs = {
        "name": name,
        "playParams": {
            "id": library_id,
            "globalId": global_id,
            "kind": "playlist",
            "isLibrary": True,
        },
    }
    if track_count:
        attrs["trackCount"] = track_count
    if curator:
        attrs["curatorName"] = curator
    return {"id": library_id, "type": "library-playlists", "attributes": attrs}


class FakeApi:
    def __init__(self, pages, playlist_tracks):
        self._pages = pages
        self._playlist_tracks = playlist_tracks

    async def get_library_playlists(self, limit: int, offset: int) -> dict:
        page_index = offset // limit
        items = self._pages[page_index] if page_index < len(self._pages) else []
        return {"data": items}

    async def get_playlist(self, playlist_id: str) -> dict:
        return {
            "data": [
                {
                    "id": playlist_id,
                    "attributes": {"name": "whatever"},
                    "relationships": {
                        "tracks": {"data": self._playlist_tracks[playlist_id]}
                    },
                }
            ]
        }


@pytest.fixture
def patch_create(monkeypatch):
    def _patch(fake_api: FakeApi):
        async def _create_from_netscape_cookies(cls, cookies_path):
            return fake_api

        monkeypatch.setattr(
            AppleMusicApi,
            "create_from_netscape_cookies",
            classmethod(_create_from_netscape_cookies),
        )

    return _patch


def test_list_playlists_single_page(patch_create):
    page = [
        _playlist_item("pl.aaa", "ALT CTRL", 12, None),
        _playlist_item("pl.bbb", "Chill", 34, "Apple Music"),
    ]
    patch_create(FakeApi(pages=[page], playlist_tracks={}))

    result = list_playlists("cookies.txt", limit=100)

    assert [p.source_id for p in result] == ["pl.aaa", "pl.bbb"]
    assert result[0].name == "ALT CTRL"
    assert result[0].track_count == 12
    assert result[0].owner is None
    assert result[1].owner == "Apple Music"


def test_list_playlists_paginates(patch_create):
    page1 = [_playlist_item(f"pl.{i}", f"Playlist {i}", i, None) for i in range(3)]
    page2 = [_playlist_item("pl.last", "Last One", 1, None)]
    patch_create(FakeApi(pages=[page1, page2], playlist_tracks={}))

    result = list_playlists("cookies.txt", limit=3)

    assert len(result) == 4
    assert result[-1].source_id == "pl.last"


def test_list_playlists_prefers_global_id_over_library_id(patch_create):
    # Matches the real get_library_playlists shape: top-level `id` and
    # playParams.id are both the library-internal `p.*` id; playParams.globalId
    # is the catalog-style `pl.*` id that profile configs actually store.
    item = _playlist_item(
        global_id="pl.0b593f1142b84a50a2c1e7088b3fb683",
        name="ALT CTRL",
        library_id="p.gek11KeiBQEoNv",
    )
    patch_create(FakeApi(pages=[[item]], playlist_tracks={}))

    result = list_playlists("cookies.txt")

    assert result[0].source_id == "pl.0b593f1142b84a50a2c1e7088b3fb683"


def test_list_playlists_falls_back_to_top_level_id_without_play_params(patch_create):
    item = {"id": "p.rawid", "type": "library-playlists", "attributes": {"name": "X"}}
    patch_create(FakeApi(pages=[[item]], playlist_tracks={}))

    result = list_playlists("cookies.txt")

    assert result[0].source_id == "p.rawid"
    assert result[0].track_count == 0


def test_get_playlist_tracks_parses_ordered_tracks(patch_create):
    tracks = [
        {
            "id": "song-1",
            "type": "library-songs",
            "attributes": {"name": "Track One", "artistName": "Artist One"},
        },
        {
            "id": "song-2",
            "type": "library-songs",
            "attributes": {"name": "Track Two", "artistName": "Artist Two"},
        },
    ]
    patch_create(FakeApi(pages=[], playlist_tracks={"pl.aaa": tracks}))

    result = get_playlist_tracks("cookies.txt", "pl.aaa")

    assert [t.source_id for t in result] == ["song-1", "song-2"]
    assert result[0].title == "Track One"
    assert result[0].artist == "Artist One"
