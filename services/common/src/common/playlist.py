from __future__ import annotations

import os
from pathlib import Path

from common.models import PlaylistEntry


# Playlist entries are stored relative to the library root (e.g.
# "music/Artist/Album/01.m4a"), so the same file works from the container
# and from the host. Entries written before this (container or host
# absolute paths) are still understood by resolve_playlist_entry.
LEGACY_CONTAINER_LIBRARY_ROOT = "/data/library"


def library_relative_entry(track_path: Path | str, library_root: Path | str) -> str:
    """A track's path as a portable, root-relative playlist entry."""
    try:
        return Path(track_path).relative_to(Path(library_root)).as_posix()
    except ValueError:
        return str(track_path)


def resolve_playlist_entry(
    entry: str,
    library_root: Path | str,
    host_root: str | None = None,
) -> str:
    """Resolves one stored playlist entry to an absolute path under the
    local library_root. Relative entries are joined onto it. Absolute
    entries from another namespace (the container's /data/library, or the
    host path in HOST_LIBRARY_ROOT) are rebased onto it. Anything else is
    returned unchanged."""
    entry = entry.strip()
    local = str(Path(library_root))
    if not entry.startswith("/"):
        return str(Path(local) / entry)
    for known_root in (host_root, LEGACY_CONTAINER_LIBRARY_ROOT):
        if known_root and (entry == known_root or entry.startswith(known_root + "/")):
            return local + entry[len(known_root):]
    return entry


def _to_relative_entry(entry: str, library_root: Path | str) -> str:
    host_root = os.environ.get("HOST_LIBRARY_ROOT") or None
    resolved = resolve_playlist_entry(entry, library_root, host_root)
    return library_relative_entry(resolved, library_root)


def playlist_entry_path(track_path: Path | str, library_root: Path | str) -> str:
    """The entry a .m3u8 should store for a track: root-relative, so it
    resolves on whichever host or container reads the playlist. See
    notes.md."""
    return library_relative_entry(track_path, library_root)


def _read_existing_entries(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and line.strip() != "#EXTM3U"
    ]


def write_m3u8(
    path: Path | str,
    track_paths: list[Path | str],
    *,
    mode: str = "absolute",
    library_root: Path | str | None = None,
) -> None:
    """Writes a .m3u8 playlist file.

    mode="absolute" (default): replaces the file's contents exactly with
    track_paths, mirroring the source playlist's current state including
    removals.

    mode="additive": preserves every entry already in the file (if any)
    and appends any new entries from track_paths not already present (by
    exact string match, in the order given). Never removes an existing
    entry, even if it's no longer present in track_paths — for source
    playlists that rotate/shrink their contents (e.g. Apple Music's
    algorithmic Mixes) where losing tracks locally just because the
    platform rotated them out isn't wanted. See notes.md.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    new_entries = [str(p) for p in track_paths]
    if mode == "absolute":
        entries = new_entries
    elif mode == "additive":
        entries = _read_existing_entries(path)
        if library_root is not None:
            entries = [_to_relative_entry(e, library_root) for e in entries]
        seen = set(entries)
        for entry in new_entries:
            if entry not in seen:
                entries.append(entry)
                seen.add(entry)
    else:
        raise ValueError(f"unknown m3u8 write mode: {mode!r}")

    lines = ["#EXTM3U", *entries]
    path.write_text("\n".join(lines) + "\n")


def prune_removed_playlists(
    profile_playlists: list[PlaylistEntry], *, playlists_root: Path | str, profile_name: str
) -> list[str]:
    """Deletes any .m3u8 under playlists_root/{profile_name} whose stem
    isn't among profile_playlists' current names.

    Nothing else in the fetch pipeline ever deletes a stale playlist file
    — each fetcher's download.py only ever calls write_m3u8() for
    playlists it's told to sync, so a playlist removed from a profile's
    own `playlists:` list otherwise sits on disk forever. sync-
    orchestrator's device sync has no independent way to know it's
    supposed to be gone either — it only ever sees whatever .m3u8 files
    physically exist under this folder, so it keeps re-syncing it.

    profile_playlists MUST be the full, unfiltered profile.playlists list
    -- never a --playlist-narrowed subset. A run scoped to specific
    playlists is choosing not to fetch the rest this time, not reporting
    they were removed from the profile (same reasoning as
    podcast_manager.download.prune_unsubscribed_shows, applied here to
    playlists instead of podcast shows). Returns the names actually
    pruned.
    """
    playlist_dir = Path(playlists_root) / profile_name
    if not playlist_dir.is_dir():
        return []
    wanted_names = {p.name for p in profile_playlists}
    pruned: list[str] = []
    for m3u8_path in sorted(playlist_dir.glob("*.m3u8")):
        if m3u8_path.stem not in wanted_names:
            m3u8_path.unlink()
            pruned.append(m3u8_path.stem)
    return pruned
