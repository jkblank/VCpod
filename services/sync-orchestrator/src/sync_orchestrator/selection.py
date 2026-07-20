"""Selective sync from an external, unmanaged library (e.g. a personal
MusicLibrary that predates and lives outside music-stack).

Deliberately does NOT use iopenpod's EngineOptions.allowed_paths for this.
allowed_paths narrows Phase 1 PC-side scanning, which shrinks seen_fps —
and fingerprint_diff_engine.py's removal planning (_plan_removed_tracks ->
_plan_orphaned_mapping_removals) treats any previously-synced fingerprint
missing from seen_fps as "removed from PC" and stages it for device
removal. Used naively, allowed_paths would propose deleting every
previously-synced track outside the new narrower scan, regardless of
whether it's still on disk. See docs/m6-ipod-headless-recommendation.md
and notes.md.

Instead, the selection is resolved into a staging directory of symlinks
that iopenpod is pointed at as a pc_folder. iopenpod only ever sees the
selected files, so its own plan reflects exactly the current selection.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def _relative_key(path: Path, library_path: Path) -> str:
    return path.relative_to(library_path).as_posix()


def _matches(relative_key: str, selection: str) -> bool:
    return relative_key == selection or relative_key.startswith(selection + "/")


def resolve_selected_files(
    library_path: Path | str,
    selections: list[str],
    *,
    mode: str = "include",
) -> tuple[list[Path], list[str]]:
    """Returns (selected_files, unresolved_selections).

    unresolved_selections lists any selection entry that matched zero
    files — most likely a typo in an artist/album/track name, surfaced
    rather than silently ignored.
    """
    library_path = Path(library_path)
    all_files = [p for p in library_path.rglob("*") if p.is_file()]

    matched: set[Path] = set()
    unresolved: list[str] = []
    for selection in selections:
        hits = [
            p for p in all_files if _matches(_relative_key(p, library_path), selection)
        ]
        if not hits:
            unresolved.append(selection)
        matched.update(hits)

    if mode == "include":
        selected = [p for p in all_files if p in matched]
    elif mode == "exclude":
        selected = [p for p in all_files if p not in matched]
    else:
        raise ValueError(f"unknown external_library mode: {mode!r}")

    return selected, unresolved


def build_staging_dir(
    staging_dir: Path | str, library_path: Path | str, selected_files: list[Path]
) -> None:
    """Fully rebuilds staging_dir as a mirror of selected_files, symlinked
    back to the real files under library_path. Full rebuild (not
    incremental) each call is what makes deselection actually take
    effect — a file removed from the selection must disappear from
    staging_dir so iopenpod's next scan stops seeing it.

    Only ever symlinks leaf files into real (non-symlink) directories —
    iopenpod's pc_library.py walks with plain os.walk (no
    followlinks=True), so a symlinked directory would silently be
    skipped, but a symlinked file inside a real directory is picked up
    normally.
    """
    staging_dir = Path(staging_dir)
    library_path = Path(library_path)

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    for src in selected_files:
        dest = staging_dir / src.relative_to(library_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.symlink_to(src)
