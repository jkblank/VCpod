from fetcher_ytmusic import api as api_module
from fetcher_ytmusic.api import _best_thumbnail_url, _library_track_count


def test_best_thumbnail_url_upscales_the_largest_available():
    thumbnails = [
        {"url": "https://yt3.googleusercontent.com/abc=w60-h60-l90-rj", "width": 60, "height": 60},
        {"url": "https://yt3.googleusercontent.com/abc=w120-h120-l90-rj", "width": 120, "height": 120},
    ]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://yt3.googleusercontent.com/abc=w1200-h1200-l90-rj"


def test_best_thumbnail_url_picks_largest_regardless_of_list_order():
    thumbnails = [
        {"url": "https://yt3.googleusercontent.com/big=w120-h120-l90-rj", "width": 120, "height": 120},
        {"url": "https://yt3.googleusercontent.com/small=w60-h60-l90-rj", "width": 60, "height": 60},
    ]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://yt3.googleusercontent.com/big=w1200-h1200-l90-rj"


def test_best_thumbnail_url_returns_none_for_empty_or_missing_thumbnails():
    assert _best_thumbnail_url([]) is None
    assert _best_thumbnail_url(None) is None


def test_best_thumbnail_url_falls_back_to_original_url_if_pattern_not_found():
    thumbnails = [{"url": "https://example.com/cover.jpg", "width": 100, "height": 100}]

    result = _best_thumbnail_url(thumbnails)

    assert result == "https://example.com/cover.jpg"


def test_library_track_count_parses_a_present_numeric_string():
    assert _library_track_count("42") == 42


def test_library_track_count_none_when_absent_or_unparseable():
    assert _library_track_count(None) is None
    assert _library_track_count("not a number") is None


class _FakeYTMusic:
    def __init__(self, library_playlists, playlist_details):
        self._library_playlists = library_playlists
        self._playlist_details = playlist_details

    def get_library_playlists(self, limit=None):
        return self._library_playlists

    def get_playlist(self, playlist_id, limit=1):
        return self._playlist_details[playlist_id]


def test_list_playlists_uses_librarys_own_count_when_present(monkeypatch):
    fake = _FakeYTMusic(
        library_playlists=[{"playlistId": "PL1", "title": "Has Count", "count": "42"}],
        playlist_details={},
    )
    monkeypatch.setattr(api_module, "YTMusic", lambda **kwargs: fake)

    result = api_module.list_playlists("oauth.json")

    assert result == [
        api_module.PlaylistSummary(source_id="PL1", name="Has Count", track_count=42, owner=None)
    ]


def test_list_playlists_fetches_real_count_when_librarys_own_count_is_missing(monkeypatch):
    # Regression: get_library_playlists' own "count" field is only ever
    # set when a playlist's YouTube Music subtitle happens to have a
    # specific 3-run shape (ytmusicapi's parse_playlist) -- confirmed
    # against the real ytmusicapi source, this is absent for plenty of
    # real playlists (e.g. ones with no description), and this project's
    # own code used to silently read that as "0 tracks" for every one of
    # them instead of fetching the real count. See notes.md.
    fake = _FakeYTMusic(
        library_playlists=[{"playlistId": "PL2", "title": "No Count"}],
        playlist_details={"PL2": {"trackCount": 17}},
    )
    monkeypatch.setattr(api_module, "YTMusic", lambda **kwargs: fake)

    result = api_module.list_playlists("oauth.json")

    assert result[0].track_count == 17


def test_list_playlists_falls_back_to_zero_when_the_detail_fetch_itself_fails(monkeypatch):
    fake = _FakeYTMusic(
        library_playlists=[{"playlistId": "PL3", "title": "Broken"}],
        playlist_details={},  # get_playlist("PL3") raises KeyError
    )
    monkeypatch.setattr(api_module, "YTMusic", lambda **kwargs: fake)

    result = api_module.list_playlists("oauth.json")

    assert result[0].track_count == 0
