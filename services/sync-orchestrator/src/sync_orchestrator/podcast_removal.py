"""Removes on-device podcast episodes once they're played (remotely via
Pocket Casts, or locally via playstate.py's device read-back) — the
device-side counterpart to podcast-manager's own played-episode file
deletion (see download.py's delete_played_episodes).

iopenpod's build_podcast_sync_plan (used for to_add in sync.py) never
proposes removals on its own — that's iopenpod's sibling
build_podcast_managed_plan, but only via a slot-based fill/clear model
(episode_slots, clear_when_listened, clear_older_than) this project
doesn't use. This module is a much smaller, targeted diff: any episode
EpisodeRecord.played already says is done, that's still actually present
on the device, gets a REMOVE_FROM_IPOD item — nothing else.

Matching by enclosure URL (falling back to title+album, same as
iopenpod's own PodcastTrackMatcher/build_podcast_sync_plan) so an
already-synced episode is recognized the same way whether the decision
is to add it or remove it. Deliberately does NOT require the episode's
local file to still exist — podcast-manager typically deletes it before
this ever runs, so keying off the state db's played flag (not file
presence) is what actually decouples the two independent side effects.
"""

from __future__ import annotations

from iopenpod.sync.contracts import SyncAction, SyncItem

from common.state import EpisodeRecord

_PODCAST_MEDIA_TYPE_FLAG = 0x04


def build_podcast_removal_items(
    episodes: list[EpisodeRecord], ipod_tracks: list[dict]
) -> list[SyncItem]:
    """Returns a REMOVE_FROM_IPOD SyncItem for every played episode still
    present on the device. Unplayed episodes, and played episodes never
    actually synced to this particular device, are left alone."""
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
    for episode in episodes:
        if not episode.played:
            continue

        track = None
        if episode.audio_url:
            track = by_enclosure.get(episode.audio_url)
        if track is None and episode.title and episode.show_name:
            track = by_title_album.get((episode.title.lower(), episode.show_name.lower()))
        if track is None:
            continue  # not (or no longer) on this device

        db_track_id = track.get("db_track_id", track.get("db_id"))
        items.append(
            SyncItem(
                action=SyncAction.REMOVE_FROM_IPOD,
                db_track_id=db_track_id,
                ipod_track=track,
                description=f"\U0001f399 {episode.show_name} — {episode.title} (played)",
            )
        )
    return items
