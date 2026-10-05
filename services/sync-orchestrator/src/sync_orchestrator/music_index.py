"""Per-profile additive index of the music a profile has written to devices.

Music on a device is limited to this index plus the profile's playlist
tracks (which iOpenPod always includes). Nothing is ever removed from the
index, so a track leaves a device only through a first-sync "remove"
decision. Each device gets its own decision, recorded in the profile's
state database. See notes.md for why.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from common.playlist import library_relative_entry
from common.state import StateDB

from sync_orchestrator.selection import build_staging_dir

DECISION_REMOVE = "remove"
DECISION_ADOPT = "adopt"
DECISIONS = (DECISION_REMOVE, DECISION_ADOPT)


def playlist_track_keys(playlists_staging: Path, music_root: Path) -> set[str]:
    keys: set[str] = set()
    for playlist in sorted(playlists_staging.glob("*.m3u8")):
        for line in playlist.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                keys.add(library_relative_entry(line, music_root))
    return keys


def under_music_root(path: str, music_root: Path) -> str | None:
    key = library_relative_entry(path, music_root)
    return None if key.startswith("/") else key


def untracked_tracks(
    matched: Iterable[str], index: set[str], playlist_keys: set[str]
) -> list[str]:
    """Tracks already on the device that neither this profile's index nor
    its playlists account for: the set the first-sync decision is about."""
    return sorted(set(matched) - index - playlist_keys)


def build_index_staging(staging: Path, music_root: Path, index: set[str]) -> list[str]:
    """Rebuilds `staging` as symlinks to the indexed tracks that still exist
    under music_root. Returns the indexed keys whose files are missing, so
    the caller can report them instead of dropping them silently."""
    present: list[Path] = []
    missing: list[str] = []
    for key in sorted(index):
        track = music_root / key
        if track.is_file():
            present.append(track)
        else:
            missing.append(key)
    build_staging_dir(staging, music_root, present)
    return missing


def record_music_writes(
    db: StateDB, written_paths: Iterable[str], music_root: Path, when: datetime
) -> int:
    keys = {key for path in written_paths if (key := under_music_root(path, music_root))}
    db.index_add(keys, when)
    return len(keys)


def apply_device_decision(
    db: StateDB, device_key: str, decision: str, when: datetime
) -> list[str]:
    """Records the first-sync decision for one device. "adopt" puts the
    tracks found on it into the index so they stay; "remove" leaves them to
    be removed by the next plan. Returns the tracks the decision covered."""
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision {decision!r}; expected one of {DECISIONS}")
    pending = db.pending_untracked(device_key)
    if decision == DECISION_ADOPT:
        db.index_add(pending, when)
    db.set_device_decision(device_key, decision, when)
    db.set_pending_untracked(device_key, [])
    return pending
