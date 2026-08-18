import httpx
import pytest

from podcast_manager import rss as rss_module
from podcast_manager.rss import fetch_rss_episodes, resolve_feed_url


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
