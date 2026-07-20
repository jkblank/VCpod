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

from typing import Any

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
    """
    results: dict[str, tuple[bool, int]] = {}
    for track in before.get("mhlt", []):
        recent_playcount = track.get("recent_playcount", 0)
        bookmark_time_ms = track.get("bookmark_time", 0)
        if not recent_playcount and not bookmark_time_ms:
            continue

        db_track_id = track.get("db_track_id", track.get("db_id"))
        if not db_track_id:
            continue

        entry = mapping.get_by_db_track_id(db_track_id)
        if entry is None:
            continue
        _fingerprint, track_mapping = entry
        source_path = track_mapping.source_path_hint
        if not source_path or source_path not in durations_by_path:
            continue

        played_up_to = bookmark_time_ms // 1000
        duration = durations_by_path[source_path]
        if recent_playcount <= 0:
            # Position moved (a resume/seek) but no completed play
            # registered this session — report progress, not "played".
            played = False
        elif duration > 0:
            played = played_up_to >= duration * PLAYED_THRESHOLD
        else:
            played = True

        results[source_path] = (played, played_up_to)

    return results
