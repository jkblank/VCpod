"""Backfills on-device artwork for podcast episodes that were already
synced before the 2026-08-26 show-cover-art fix existed (download.py's
per-show cover.* fetch, `fetch_feed_image_url`).

iopenpod's own build_podcast_sync_plan (used for to_add in sync.py) only
ever emits ADD_TO_IPOD items for episodes not yet on the device -- an
episode already synced is filtered out and never revisited, no matter
what artwork shows up next to its file afterward. Confirmed live
2026-09-01: the cover.* fix only benefits episodes added *after* it
landed; the ~1600+ episodes already on a real device stayed art-less
indefinitely with no existing code path to fix them. See notes.md.

Matching by enclosure URL (falling back to title+album) mirrors
podcast_removal.py/build_podcast_sync_plan exactly, so an episode
already on this device is recognized identically regardless of which of
these three modules is looking at it.

Gates on the on-device track's own artwork_count/artwork_id_ref rather
than any locally-cached hash -- naturally idempotent across repeated
syncs: once a real write succeeds, the next sync's device read-back
reports nonzero artwork state and this episode is never proposed again,
with no separate "already handled" bookkeeping needed.
"""

from __future__ import annotations

from iopenpod.artworkdb_writer.art_extractor import art_hash, extract_art_with_folder
from iopenpod.podcasts.models import PodcastFeed
from iopenpod.sync.contracts import SyncAction, SyncItem

_PODCAST_MEDIA_TYPE_FLAG = 0x04


def build_podcast_artwork_backfill_items(
    feeds: list[PodcastFeed], ipod_tracks: list[dict]
) -> tuple[list[SyncItem], dict[int, str]]:
    """Returns (to_update_artwork items, matched_pc_paths) for already-
    synced podcast episodes whose on-device track currently has no
    artwork but whose local file resolves one via extract_art_with_folder
    (embedded art, or the show-level cover.* fallback).

    matched_pc_paths must be merged into SyncPlan.matched_pc_paths -- the
    artwork writer only re-encodes a to_update_artwork item whose
    db_track_id has a matching PC-side source path there (see
    artwork_writer.py's _collect_track_artwork_decisions)."""
    by_enclosure: dict[str, dict] = {}
    by_title_album: dict[tuple[str, str], dict] = {}
    for track in ipod_tracks:
        if not (track.get("media_type", 0) & _PODCAST_MEDIA_TYPE_FLAG):
            continue
        enclosure_url = track.get("Podcast Enclosure URL", "")
        if enclosure_url:
            by_enclosure[enclosure_url] = track
        title = track.get("Title", "")
        album = track.get("Album", "")
        if title and album:
            by_title_album[(title.lower(), album.lower())] = track

    items: list[SyncItem] = []
    matched_pc_paths: dict[int, str] = {}
    for feed in feeds:
        for episode in feed.episodes:
            if not episode.downloaded_path:
                continue

            track = None
            if episode.audio_url:
                track = by_enclosure.get(episode.audio_url)
            if track is None and episode.title and feed.title:
                track = by_title_album.get((episode.title.lower(), feed.title.lower()))
            if track is None:
                continue  # not on this device yet -- build_podcast_sync_plan handles it

            if track.get("artwork_count", 0) or track.get("artwork_id_ref", 0):
                continue  # already has artwork on-device

            art_bytes = extract_art_with_folder(episode.downloaded_path)
            if not art_bytes:
                continue  # no embedded art, no show-level cover.* either

            db_track_id = track.get("db_track_id", track.get("db_id"))
            if not db_track_id:
                continue

            items.append(
                SyncItem(
                    action=SyncAction.UPDATE_ARTWORK,
                    db_track_id=db_track_id,
                    ipod_track=track,
                    old_art_hash=None,
                    new_art_hash=art_hash(art_bytes),
                    description=f"\U0001f3a8 {feed.title} — {episode.title}",
                )
            )
            matched_pc_paths[db_track_id] = episode.downloaded_path

    return items, matched_pc_paths
