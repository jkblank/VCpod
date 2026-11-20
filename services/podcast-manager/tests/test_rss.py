import httpx
import pytest

from podcast_manager import rss as rss_module
from podcast_manager.rss import (
    _parse_itunes_duration,
    fetch_feed_image_url,
    fetch_rss_episodes,
    resolve_feed_url,
    search_podcasts,
)


class FakeResponse:
    def __init__(self, *, json_data: dict | None = None, content: bytes = b""):
        self._json_data = json_data
        self.content = content

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._json_data


_SAMPLE_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
<channel>
<title>Test Show</title>
<item>
<title>Episode One</title>
<description>A real episode description.</description>
<itunes:episode>3</itunes:episode>
<itunes:season>2</itunes:season>
<itunes:duration>01:02:03</itunes:duration>
<pubDate>Thu, 13 Aug 2026 15:00:00 -0000</pubDate>
<enclosure url="https://cdn.example.com/ep1.mp3" length="123" type="audio/mpeg"/>
</item>
<item>
<title>Episode Without Numbers</title>
<description>No itunes:episode/season tags on this one.</description>
<pubDate>Thu, 06 Aug 2026 15:00:00 -0000</pubDate>
<enclosure url="https://cdn.example.com/ep2.mp3" length="456" type="audio/mpeg"/>
</item>
<item>
<title>No Enclosure</title>
<description>Skipped -- no audio to match against.</description>
</item>
</channel>
</rss>
"""


def test_resolve_feed_url_matches_exact_title_and_author(monkeypatch):
    def fake_get(url, params, timeout=None):
        assert url == rss_module._ITUNES_SEARCH_URL
        assert params["entity"] == "podcast"
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "collectionName": "Some Other Show",
                        "artistName": "Someone Else",
                        "feedUrl": "https://example.com/wrong.xml",
                    },
                    {
                        "collectionName": "The Standup with ThePrimeagen",
                        "artistName": "ThePrimeagen",
                        "feedUrl": "https://rss2.flightcast.com/real.xml",
                    },
                ]
            }
        )

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    result = resolve_feed_url("The Standup with ThePrimeagen", "ThePrimeagen")

    assert result == "https://rss2.flightcast.com/real.xml"


def test_resolve_feed_url_falls_back_to_top_result_when_no_exact_match(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, params, timeout=None: FakeResponse(
            json_data={
                "results": [
                    {
                        "collectionName": "Slightly Different Title",
                        "artistName": "Slightly Different Author",
                        "feedUrl": "https://example.com/best-guess.xml",
                    }
                ]
            }
        ),
    )

    result = resolve_feed_url("My Show", "My Author")

    assert result == "https://example.com/best-guess.xml"


def test_resolve_feed_url_returns_none_when_no_results(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, params, timeout=None: FakeResponse(json_data={"results": []}),
    )

    assert resolve_feed_url("Nonexistent Show", "Nobody") is None


def test_resolve_feed_url_returns_none_on_http_error(monkeypatch):
    def fake_get(url, params, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    assert resolve_feed_url("Any Show", "Any Author") is None


def test_fetch_rss_episodes_parses_description_and_itunes_tags(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(content=_SAMPLE_FEED),
    )

    episodes = fetch_rss_episodes("https://example.com/feed.xml")

    assert len(episodes) == 2  # the enclosure-less item is skipped
    first = episodes[0]
    assert first.enclosure_url == "https://cdn.example.com/ep1.mp3"
    assert first.title == "Episode One"
    assert first.description == "A real episode description."
    assert first.episode_number == 3
    assert first.season_number == 2
    assert first.published == "Thu, 13 Aug 2026 15:00:00 -0000"
    assert first.duration_seconds == 3723  # 1h2m3s


def test_fetch_rss_episodes_treats_missing_itunes_tags_as_none(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(content=_SAMPLE_FEED),
    )

    episodes = fetch_rss_episodes("https://example.com/feed.xml")

    second = episodes[1]
    assert second.episode_number is None
    assert second.season_number is None
    assert second.duration_seconds is None


def test_fetch_rss_episodes_returns_empty_on_malformed_xml(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(content=b"not xml at all"),
    )

    assert fetch_rss_episodes("https://example.com/broken.xml") == []


def test_fetch_rss_episodes_returns_empty_on_http_error(monkeypatch):
    def fake_get(url, timeout=None, follow_redirects=True):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    assert fetch_rss_episodes("https://example.com/feed.xml") == []


_FEED_WITH_ITUNES_IMAGE = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
<channel>
<title>Test Show</title>
<itunes:image href="https://cdn.example.com/itunes-cover.jpg"/>
<image><url>https://cdn.example.com/plain-cover.jpg</url></image>
</channel>
</rss>
"""

_FEED_WITH_PLAIN_IMAGE_ONLY = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Test Show</title>
<image><url>https://cdn.example.com/plain-cover.jpg</url></image>
</channel>
</rss>
"""


def test_fetch_feed_image_url_prefers_itunes_image_over_plain_image(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(
            content=_FEED_WITH_ITUNES_IMAGE
        ),
    )

    assert fetch_feed_image_url("https://example.com/feed.xml") == "https://cdn.example.com/itunes-cover.jpg"


def test_fetch_feed_image_url_falls_back_to_plain_image(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(
            content=_FEED_WITH_PLAIN_IMAGE_ONLY
        ),
    )

    assert fetch_feed_image_url("https://example.com/feed.xml") == "https://cdn.example.com/plain-cover.jpg"


def test_fetch_feed_image_url_returns_none_when_no_image_tag(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, timeout=None, follow_redirects=True: FakeResponse(content=_SAMPLE_FEED),
    )

    assert fetch_feed_image_url("https://example.com/feed.xml") is None


def test_fetch_feed_image_url_returns_none_on_http_error(monkeypatch):
    def fake_get(url, timeout=None, follow_redirects=True):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    assert fetch_feed_image_url("https://example.com/feed.xml") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("01:02:03", 3723),
        ("02:03", 123),
        ("45", 45),
        (None, None),
        ("", None),
        ("not a duration", None),
        ("1:2:3:4", None),
    ],
)
def test_parse_itunes_duration(value, expected):
    assert _parse_itunes_duration(value) == expected


def test_search_podcasts_returns_every_result_with_a_feed_url(monkeypatch):
    def fake_get(url, params, timeout=None):
        assert url == rss_module._ITUNES_SEARCH_URL
        assert params == {"term": "linux", "entity": "podcast", "limit": 15}
        return FakeResponse(
            json_data={
                "results": [
                    {
                        "collectionId": 111,
                        "collectionName": "Linux Lads",
                        "artistName": "Linux Lads",
                        "feedUrl": "https://example.com/linux-lads.xml",
                        "artworkUrl600": "https://example.com/linux-lads.jpg",
                    },
                    {
                        "collectionId": 222,
                        "collectionName": "No Feed Here",
                        "artistName": "Nobody",
                        # no feedUrl -- Apple-exclusive shows do this in practice
                    },
                ]
            }
        )

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    results = search_podcasts("linux")

    assert len(results) == 1
    assert results[0].collection_id == 111
    assert results[0].title == "Linux Lads"
    assert results[0].author == "Linux Lads"
    assert results[0].feed_url == "https://example.com/linux-lads.xml"
    assert results[0].artwork_url == "https://example.com/linux-lads.jpg"


def test_search_podcasts_falls_back_to_artwork_url_100(monkeypatch):
    monkeypatch.setattr(
        rss_module.httpx,
        "get",
        lambda url, params, timeout=None: FakeResponse(
            json_data={
                "results": [
                    {
                        "collectionId": 1,
                        "collectionName": "Show",
                        "artistName": "Author",
                        "feedUrl": "https://example.com/show.xml",
                        "artworkUrl100": "https://example.com/small.jpg",
                    }
                ]
            }
        ),
    )

    results = search_podcasts("show")

    assert results[0].artwork_url == "https://example.com/small.jpg"


def test_search_podcasts_respects_limit_param(monkeypatch):
    captured = {}

    def fake_get(url, params, timeout=None):
        captured.update(params)
        return FakeResponse(json_data={"results": []})

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    search_podcasts("linux", limit=5)

    assert captured["limit"] == 5


def test_search_podcasts_raises_on_http_error(monkeypatch):
    # Unlike resolve_feed_url (best-effort background enrichment),
    # search_podcasts is a direct user-initiated action -- a real
    # failure must surface to the caller, not degrade to [].
    def fake_get(url, params, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(rss_module.httpx, "get", fake_get)

    with pytest.raises(httpx.ConnectError):
        search_podcasts("linux")
