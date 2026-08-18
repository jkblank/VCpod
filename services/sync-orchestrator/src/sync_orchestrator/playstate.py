"""Resolves real, on-device podcast listening progress back to Pocket
Casts episode identities — the read side of M8's play-status round trip.

iopenpod's load_ipod_library() (merge_playcounts=True, the default)
already parses the device's Play Counts file and folds deltas into each
mhit track dict in memory: recent_playcount, bookmark_time (ms), rating,
last_played — confirmed read-only (never deletes/modifies the source
file), so this is safe to call on every plan, not just --execute runs.
See notes.md's M8 write-up.

A device track only carries a db_track_id, not our own dedup tags — the
bridge back to a PC-side file is iopenpod's own sync/mapping.py
MappingFile, keyed the same way fingerprint_diff_engine.py's own
ipod_by_db_track_id lookup is built.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Fraction of an episode's duration a bookmark position must reach to be
# considered "played through" rather than merely "in progress" — matches
# how podcast apps generally distinguish resume-worthy episodes from
# finished ones. Only applied when a real duration is known; otherwise
# recent_playcount > 0 alone is trusted (mirrors the old, cruder signal).
PLAYED_THRESHOLD = 0.9


def resolve_played_states(
    before: dict[str, Any],
    mapping: Any,
    durations_by_path: dict[str, int],
) -> dict[str, tuple[bool, int]]:
    """Returns {local_path: (played, played_up_to_seconds)} for every
    device track with a real play-state delta since the last sync that
    resolves back to a known local podcast episode file. durations_by_path
    maps a local_path to its known duration in seconds (0/absent = unknown
    duration for that path — such paths still resolve, just always treated
    as fully played on any recent_playcount, per PLAYED_THRESHOLD's
    fallback).

    Paths not present in durations_by_path (i.e. not a known podcast
    episode — most commonly a music track) are silently skipped: this is
    how music playback naturally doesn't get treated as podcast state
    without needing an explicit is-this-a-podcast check.

    Matched by filename, not full path: iopenpod's own mapping file
    (iOpenPod.json) stores TrackMapping.source_path_hint as a bare
    filename, not the absolute path our own state db's local_path uses --
    confirmed live (2026-08-02) via the debug logging below, where every
    single podcast episode with real device activity failed the old
    full-path membership check, 8/8, on this exact mismatch. Safe to match
    on filename alone because our own episode filenames already embed the
    Pocket Casts episode UUID (see download.py's _episode_path), which is
    globally unique -- no two episodes can collide. See notes.md.
    """
    durations_by_basename = {Path(p).name: p for p in durations_by_path}
    results: dict[str, tuple[bool, int]] = {}
    for track in before.get("mhlt", []):
        recent_playcount = track.get("recent_playcount", 0)
        bookmark_time_ms = track.get("bookmark_time", 0)
        # play_count_1 is the device's own cumulative, persistent play
        # counter (iopenpod's itunesdb_parser/playcounts.py: "the new
        # cumulative play count", incremented by and never reset by a
        # merge) -- unlike recent_playcount/bookmark_time (this run's
        # ephemeral Play Counts-file delta, confirmed reset to 0 once an
        # earlier commit has already merged it), it's the only reliable
        # signal left once that delta is gone. Confirmed live (2026-08-18):
        # several episodes were genuinely, fully played on-device
        # (play_count_1 > 0) but their own commit had already consumed
        # the ephemeral delta before our local db ever recorded the
        # play, silently leaving them "unplayed" locally and on Pocket
        # Casts with no further signal to detect it by -- required
        # manual reconciliation every time. See notes.md.
        cumulative_play_count = track.get("play_count_1", 0) or 0
        if not recent_playcount and not bookmark_time_ms and not cumulative_play_count:
            continue

        db_track_id = track.get("db_track_id", track.get("db_id"))
        if not db_track_id:
            logger.debug(
                "activity but no db_track_id: recent_playcount=%r bookmark_time_ms=%r "
                "play_count_1=%r",
                recent_playcount, bookmark_time_ms, cumulative_play_count,
            )
            continue

        entry = mapping.get_by_db_track_id(db_track_id)
        if entry is None:
            logger.debug(
                "db_track_id=%s recent_playcount=%r bookmark_time_ms=%r "
                "play_count_1=%r: no mapping entry (iOpenPod.json has no "
                "TrackMapping for this id)",
                db_track_id, recent_playcount, bookmark_time_ms, cumulative_play_count,
            )
            continue
        _fingerprint, track_mapping = entry
        source_path = track_mapping.source_path_hint
        if not source_path:
            logger.debug(
                "db_track_id=%s: mapping entry has no source_path_hint", db_track_id,
            )
            continue
        source_basename = Path(source_path).name
        local_path = durations_by_basename.get(source_basename)
        if local_path is None:
            logger.debug(
                "db_track_id=%s source_path=%r (basename=%r): not a known "
                "podcast episode filename -- most commonly a music track",
                db_track_id, source_path, source_basename,
            )
            continue

        played_up_to = bookmark_time_ms // 1000
        duration = durations_by_path[local_path]
        if duration > 0:
            # Position-based check, independent of recent_playcount: a
            # real report was a played-but-not-removed episode after the
            # user pressed skip/next about a minute before the end of an
            # hour-long episode — the likely explanation is a click-wheel
            # iPod's own play-count only incrementing on a *natural*
            # track completion, not on skip/next, leaving recent_playcount
            # at 0 even with bookmark_time reflecting ~98% completion
            # (not independently verified against raw device Play Counts
            # data, since no device was connected at diagnosis time — but
            # trusting position alone once past PLAYED_THRESHOLD is safe
            # regardless: you don't reach 90%+ of a real episode's
            # duration via an idle scrub, and even a deliberate
            # seek-to-near-the-end is a reasonable case to treat as
            # "done" too). See notes.md.
            played = played_up_to >= duration * PLAYED_THRESHOLD
        elif recent_playcount > 0:
            # No known duration to compare position against — fall back
            # to the device's own playcount signal.
            played = True
        else:
            # Position moved (a resume/seek) but no completed play
            # registered this session, and no duration to judge position
            # against — report progress, not "played".
            played = False

        if cumulative_play_count > 0:
            # Overrides the position/delta-based result above: a nonzero
            # cumulative play_count_1 means this track has completed at
            # least one real play *ever*, even if this run's own
            # ephemeral delta and position say otherwise (already
            # consumed by an earlier commit, or reset to 0 after a
            # natural completion). Never the reverse — a zero
            # cumulative count must not downgrade a played=True the
            # position/delta check already established (e.g. a partial
            # re-listen of an episode already fully completed once
            # before should still count as played).
            played = True

        logger.debug(
            "db_track_id=%s local_path=%r: recent_playcount=%r bookmark_time_ms=%r "
            "play_count_1=%r duration=%r -> played=%r played_up_to=%r",
            db_track_id, local_path, recent_playcount, bookmark_time_ms,
            cumulative_play_count, duration, played, played_up_to,
        )
        results[local_path] = (played, played_up_to)

    return results
