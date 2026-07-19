from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from common.playlist import write_m3u8
from common.state import StateDB

from library_manager.scan import TrackInfo

FIDELITY_ORDER = ("apple_music", "spotify", "ytmusic")
DEFAULT_FUZZY_THRESHOLD = 92.0


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _similarity(a: TrackInfo, b: TrackInfo) -> float:
    key_a = f"{_normalize(a.artist)} {_normalize(a.title)}"
    key_b = f"{_normalize(b.artist)} {_normalize(b.title)}"
    return fuzz.token_sort_ratio(key_a, key_b)


def find_duplicate_groups(
    tracks: list[TrackInfo], fuzzy_threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> list[list[TrackInfo]]:
    """Groups tracks that represent the same song from different sources.
    Same-source tracks are never grouped against each other — the fetchers'
    own state-db skip-if-known logic already prevents same-source
    duplication, so this is purely a cross-source concern."""
    groups: list[list[TrackInfo]] = []
    grouped_ids: set[int] = set()

    by_isrc: dict[str, list[TrackInfo]] = {}
    for t in tracks:
        if t.isrc:
            by_isrc.setdefault(t.isrc, []).append(t)
    for isrc_group in by_isrc.values():
        if len({t.source for t in isrc_group}) >= 2:
            groups.append(isrc_group)
            grouped_ids.update(id(t) for t in isrc_group)

    remaining = [t for t in tracks if id(t) not in grouped_ids]
    used: set[int] = set()
    for i, a in enumerate(remaining):
        if id(a) in used:
            continue
        group = [a]
        for b in remaining[i + 1 :]:
            if id(b) in used or b.source == a.source:
                continue
            if _similarity(a, b) >= fuzzy_threshold:
                group.append(b)
                used.add(id(b))
        if len(group) >= 2:
            used.add(id(a))
            groups.append(group)

    return groups


def choose_canonical(group: list[TrackInfo]) -> TrackInfo:
    def rank(t: TrackInfo) -> int:
        try:
            return FIDELITY_ORDER.index(t.source)
        except ValueError:
            return len(FIDELITY_ORDER)

    return min(group, key=rank)


@dataclass
class DedupResult:
    canonical: list[TrackInfo] = field(default_factory=list)
    quarantined: list[tuple[TrackInfo, Path]] = field(default_factory=list)


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _rewrite_playlists(playlists_root: Path, path_rewrites: dict[str, str]) -> None:
    for m3u8_path in playlists_root.rglob("*.m3u8"):
        lines = m3u8_path.read_text(encoding="utf8").splitlines()
        track_lines = [line for line in lines if line != "#EXTM3U"]
        new_track_lines = [path_rewrites.get(line, line) for line in track_lines]
        if new_track_lines != track_lines:
            write_m3u8(m3u8_path, new_track_lines)


def quarantine_duplicates(
    groups: list[list[TrackInfo]],
    *,
    library_root: Path | str,
    playlists_root: Path | str,
    state_db_paths: list[Path | str],
) -> DedupResult:
    library_root = Path(library_root)
    playlists_root = Path(playlists_root)
    result = DedupResult()
    path_rewrites: dict[str, str] = {}

    for group in groups:
        canonical = choose_canonical(group)
        result.canonical.append(canonical)
        for track in group:
            if track is canonical:
                continue

            quarantine_dir = library_root / ".duplicates" / track.source
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            dest = _dedupe_path(quarantine_dir / f"{track.source_id}{track.path.suffix}")

            path_rewrites[str(track.path)] = str(canonical.path)
            track.path.rename(dest)
            os.utime(dest, None)  # reset mtime to now — starts the cleanup countdown

            result.quarantined.append((track, dest))

            for state_db_path in state_db_paths:
                with StateDB(state_db_path) as db:
                    db.update_local_path(track.source, track.source_id, str(canonical.path))

    if path_rewrites:
        _rewrite_playlists(playlists_root, path_rewrites)

    return result
