from __future__ import annotations

from pathlib import Path


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
        seen = set(entries)
        for entry in new_entries:
            if entry not in seen:
                entries.append(entry)
                seen.add(entry)
    else:
        raise ValueError(f"unknown m3u8 write mode: {mode!r}")

    lines = ["#EXTM3U", *entries]
    path.write_text("\n".join(lines) + "\n")
