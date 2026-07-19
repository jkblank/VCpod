from __future__ import annotations

import time
from pathlib import Path

_QUARANTINE_DIRNAME = ".duplicates"


def sweep_quarantine(
    library_root: Path | str, *, older_than_days: int = 14, dry_run: bool = False
) -> list[Path]:
    """Permanently deletes quarantined duplicate files older than the given
    cutoff (measured from when they were quarantined, i.e. the file's own
    mtime — dedup.quarantine_duplicates resets it at quarantine time).
    Returns the list of files removed (or that would be removed, if
    dry_run)."""
    library_root = Path(library_root)
    quarantine_dir = library_root / _QUARANTINE_DIRNAME
    if not quarantine_dir.is_dir():
        return []

    cutoff = time.time() - older_than_days * 86400
    removed: list[Path] = []
    for path in quarantine_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.stat().st_mtime <= cutoff:
            removed.append(path)
            if not dry_run:
                path.unlink()
    return removed
