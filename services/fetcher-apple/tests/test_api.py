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
    # Real get_library_playlists responses never actually carry a
    # trackCount attribute at all (confirmed live against a real
    # account) -- track_count here instead seeds FakeApi's
    # get_library_playlist fixture (keyed by library_id), exactly
    # mirroring the real, separate per-playlist request the code under
    # test now makes to get a real count. See notes.md.
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
    if curator:
        attrs["curatorName"] = curator
    return {"id": library_id, "type": "library-playlists", "attributes": attrs}, track_count


class FakeApi:
    def __init__(self, pages, playlist_tracks, track_counts=None, fail_track_count_for=()):
        # pages: list[list[item]] (items only, track_count already
        # folded into track_counts by the fixture helpers below).
        self._pages = pages
        self._playlist_tracks = playlist_tracks
        self._track_counts = track_counts or {}
        self._fail_track_count_for = set(fail_track_count_for)
        self.track_count_requests: list[str] = []

    async def get_library_playlists(self, limit: int, offset: int) -> dict:
        page_index = offset // limit
        items = self._pages[page_index] if page_index < len(self._pages) else []
        return {"data": items}

    async def get_library_playlist(self, playlist_id: str, include: str, limit: int) -> dict:
        self.track_count_requests.append(playlist_id)
        if playlist_id in self._fail_track_count_for:
            raise RuntimeError("simulated API failure")
        return {
            "data": [
                {
                    "id": playlist_id,
                    "relationships": {
                        "tracks": {
                            "data": [],
                            "meta": {"total": self._track_counts.get(playlist_id, 0)},
                        }
                    },
                }
            ]
        }

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


def _page(*item_and_counts):
    """Splits [(item, track_count), ...] into (items, track_counts_dict) --
    items go straight into FakeApi.pages, counts get merged into one
    track_counts dict across every page passed to a given FakeApi."""
    items = [item for item, _ in item_and_counts]
    counts = {item["id"]: count for item, count in item_and_counts}
    return items, counts


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
    items, counts = _page(
        _playlist_item("pl.aaa", "ALT CTRL", 12, None),
        _playlist_item("pl.bbb", "Chill", 34, "Apple Music"),
    )
    patch_create(FakeApi(pages=[items], playlist_tracks={}, track_counts=counts))

    result = list_playlists("cookies.txt", limit=100)

    assert [p.source_id for p in result] == ["pl.aaa", "pl.bbb"]
    assert result[0].name == "ALT CTRL"
    assert result[0].track_count == 12
    assert result[0].owner is None
    assert result[1].owner == "Apple Music"


def test_list_playlists_paginates(patch_create):
    page1_items, page1_counts = _page(
        *(_playlist_item(f"pl.{i}", f"Playlist {i}", i, None) for i in range(3))
    )
    page2_items, page2_counts = _page(_playlist_item("pl.last", "Last One", 1, None))
    patch_create(
        FakeApi(
            pages=[page1_items, page2_items],
            playlist_tracks={},
            track_counts={**page1_counts, **page2_counts},
        )
    )

    result = list_playlists("cookies.txt", limit=3)

    assert len(result) == 4
    assert result[-1].source_id == "pl.last"


def test_list_playlists_prefers_global_id_over_library_id(patch_create):
    # Matches the real get_library_playlists shape: top-level `id` and
    # playParams.id are both the library-internal `p.*` id; playParams.globalId
    # is the catalog-style `pl.*` id that profile configs actually store.
    items, counts = _page(
        _playlist_item(
            global_id="pl.0b593f1142b84a50a2c1e7088b3fb683",
            name="ALT CTRL",
            library_id="p.gek11KeiBQEoNv",
        )
    )
    fake = FakeApi(pages=[items], playlist_tracks={}, track_counts=counts)
    patch_create(fake)

    result = list_playlists("cookies.txt")

    assert result[0].source_id == "pl.0b593f1142b84a50a2c1e7088b3fb683"
    # The real per-playlist detail request must use the library-internal
    # `p.*` id, not the `pl.*` catalog id this function returns to
    # callers -- get_library_playlist 404s on the latter in real life.
    assert fake.track_count_requests == ["p.gek11KeiBQEoNv"]


def test_list_playlists_falls_back_to_top_level_id_without_play_params(patch_create):
    item = {"id": "p.rawid", "type": "library-playlists", "attributes": {"name": "X"}}
    patch_create(FakeApi(pages=[[item]], playlist_tracks={}))

    result = list_playlists("cookies.txt")

    assert result[0].source_id == "p.rawid"
    assert result[0].track_count == 0


def test_list_playlists_ignores_any_trackcount_attribute_and_fetches_the_real_one(
    patch_create,
):
    # Regression: a real get_library_playlists response never actually
    # has a trackCount attribute at all (confirmed live) -- this used to
    # read attrs.get("trackCount", 0), always silently landing on 0.
    # Even if some future/different response shape did include one, the
    # real per-playlist count must win, not a stale/absent bulk-listing
    # field. See notes.md.
    item = {
        "id": "p.has-bogus-attr",
        "type": "library-playlists",
        "attributes": {"name": "X", "trackCount": 999},
    }
    patch_create(
        FakeApi(pages=[[item]], playlist_tracks={}, track_counts={"p.has-bogus-attr": 7})
    )

    result = list_playlists("cookies.txt")

    assert result[0].track_count == 7


def test_list_playlists_falls_back_to_zero_when_track_count_request_fails(patch_create):
    item = {"id": "p.broken", "type": "library-playlists", "attributes": {"name": "X"}}
    patch_create(FakeApi(pages=[[item]], playlist_tracks={}, fail_track_count_for={"p.broken"}))

    result = list_playlists("cookies.txt")

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
