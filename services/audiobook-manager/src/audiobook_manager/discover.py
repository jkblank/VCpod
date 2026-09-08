"""Scans an external "drop zone" folder (raw, not-yet-processed
audiobook parts -- see README's manual Libby-capture workflow) for
subfolders that look like audiobook candidates, and tracks which ones
`import_audiobook`/`beet import` has already successfully processed so
repeat scans distinguish "still needs processing" from "already in
library/audiobooks".

Deliberately has no beets import anywhere in this module (unlike
beets_import.py, whose own `from beets.library import Library` is
lazy/inside a function body) -- callers like web-gui-backend that only
need to *list* discovered books, not actually run the merge+tag
pipeline, should never pay for importing beets' own dependency tree
just by importing this module."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

_AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4b", ".flac", ".wav", ".aac", ".ogg"}


def _state_path(state_root: Path | str) -> Path:
    return Path(state_root) / "audiobooks" / "discovered_state.json"


def _load_state(state_root: Path | str) -> dict:
    path = _state_path(state_root)
    if not path.is_file():
        return {"imported": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state_root: Path | str, state: dict) -> None:
    path = _state_path(state_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(path)


def record_import(
    state_root: Path | str, source_name: str, imported_paths: list[Path]
) -> None:
    """Called after a successful import-audiobook/tag run so future
    discover_audiobooks() calls know this source folder has already
    been processed. Keyed by the parts-dir's own folder name (e.g.
    "Franz Kafka - The Trial") -- the only identifier discover has to
    go on when scanning an arbitrary external root, and the same name
    import-audiobook's own staging dir preserves (see cli.py) -- not by
    absolute path, since the same folder might get rescanned from a
    different location (a moved drive, a redeployed server)."""
    state = _load_state(state_root)
    state["imported"][source_name] = {
        "imported_at": time.time(),
        "library_paths": [str(p) for p in imported_paths],
    }
    _save_state(state_root, state)


@dataclass
class DiscoveredBook:
    name: str
    path: str
    audio_file_count: int
    already_imported: bool
    imported_at: float | None
    library_paths: list[str]


def discover_audiobooks(
    root: Path | str, state_root: Path | str
) -> list[DiscoveredBook]:
    """Lists every immediate subdirectory of root containing at least
    one audio file -- one folder per book, matching the manual capture
    workflow's own "Author - Title" convention (README's example) --
    cross-referenced against record_import()'s state so repeat scans
    tell "new" apart from "already processed". root not existing (e.g.
    the drop zone hasn't been configured/mounted yet) returns an empty
    list, not an error -- same "not a misconfiguration" treatment
    resolve_audiobooks_folder gives a missing library_root/audiobooks."""
    root = Path(root)
    if not root.is_dir():
        return []

    imported = _load_state(state_root).get("imported", {})

    books: list[DiscoveredBook] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        audio_files = [
            p
            for p in entry.iterdir()
            if p.is_file() and p.suffix.lower() in _AUDIO_EXTENSIONS
        ]
        if not audio_files:
            continue
        record = imported.get(entry.name)
        books.append(
            DiscoveredBook(
                name=entry.name,
                path=str(entry),
                audio_file_count=len(audio_files),
                already_imported=record is not None,
                imported_at=record["imported_at"] if record else None,
                library_paths=record["library_paths"] if record else [],
            )
        )
    return books
