"""RSS-sourced episode metadata (description, episode/season number,
published date) — Pocket Casts' own API doesn't expose any of these
(confirmed live against /podcast/full/, see notes.md), so this resolves
each show's real RSS feed independently and parses it directly.

Best-effort throughout: a show whose feed can't be resolved, or an item
that fails to parse, degrades to blank/None metadata rather than
blocking a sync — same resilience pattern already used for per-episode
download failures in download.py.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
_REQUEST_TIMEOUT = httpx.Timeout(10.0, connect=15.0, read=20.0)


@dataclass
class RssEpisodeMeta:
    enclosure_url: str
    title: str
    description: str
    episode_number: int | None
    season_number: int | None
    published: str | None


def resolve_feed_url(title: str, author: str) -> str | None:
    """Looks up a show's real RSS feed URL via Apple's public, key-free
    iTunes Search API, matching by author + title. Pocket Casts exposes
    no feed/RSS URL of its own (confirmed live) -- this is the only
    unauthenticated, no-registration way found to resolve one. Returns
    None (never raises) on any failure or no confident match, so one
    unresolvable show doesn't block the rest of a sync."""
    try:
        resp = httpx.get(
            _ITUNES_SEARCH_URL,
            params={"term": f"{title} {author}", "entity": "podcast", "limit": 5},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("rss: could not search for feed of %r (%r): %s", title, author, exc)
        return None

    title_cf = title.casefold()
    author_cf = author.casefold()
    for result in results:
        collection_name = (result.get("collectionName") or "").casefold()
        artist_name = (result.get("artistName") or "").casefold()
        if collection_name == title_cf or artist_name == author_cf:
            feed_url = result.get("feedUrl")
            if feed_url:
                return feed_url

    if results:
        # No exact match, but at least one result — take the top hit's
        # feed rather than nothing, iTunes Search already ranks by
        # relevance to the query. Still logged so a wrong match is
        # traceable rather than silently attaching another show's data.
        feed_url = results[0].get("feedUrl")
        if feed_url:
            logger.debug(
                "rss: no exact match for %r (%r), using top search result %r",
                title, author, results[0].get("collectionName"),
            )
            return feed_url

    logger.warning("rss: no feed found for %r (%r)", title, author)
    return None


def _find_itunes_tag(item: ET.Element, tag: str) -> int | None:
    value = item.findtext(f"{{{_ITUNES_NS}}}{tag}")
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def fetch_rss_episodes(feed_url: str) -> list[RssEpisodeMeta]:
    """Fetches and parses a podcast RSS feed. Returns [] (logged) on any
    fetch or parse failure -- never raises, matching resolve_feed_url's
    same "one bad show doesn't block the sync" contract."""
    try:
        resp = httpx.get(feed_url, timeout=_REQUEST_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except (httpx.HTTPError, ET.ParseError) as exc:
        logger.warning("rss: could not fetch/parse feed %r: %s", feed_url, exc)
        return []

    episodes: list[RssEpisodeMeta] = []
    for item in root.findall(".//item"):
        enclosure = item.find("enclosure")
        enclosure_url = enclosure.get("url") if enclosure is not None else None
        if not enclosure_url:
            continue
        episodes.append(
            RssEpisodeMeta(
                enclosure_url=enclosure_url,
                title=item.findtext("title", default=""),
                description=item.findtext("description", default=""),
                episode_number=_find_itunes_tag(item, "episode"),
                season_number=_find_itunes_tag(item, "season"),
                published=item.findtext("pubDate"),
            )
        )
    return episodes
