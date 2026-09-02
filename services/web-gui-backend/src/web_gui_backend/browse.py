"""Shared, security-scoped directory listing -- backs both the External
library screen (an arbitrary path the user points at, outside anything
this project otherwise manages) and the Audiobooks screen
(library_root/audiobooks, a real but still user-relevant tree). Both
need the same "browse and tick a folder" UX (matching the
Sources/Podcasts pickers' own browse-then-select pattern), just rooted
at a different real filesystem path.

Every listing is strictly confined to the given root -- a subpath that
would resolve outside it (a literal ".." segment, a symlink escape) is
rejected, not silently followed. This is the one place in the backend
that reads an arbitrary, user-supplied filesystem location (every other
route only ever touches config_root/library_root-relative paths this
project itself manages), so it gets its own explicit safety check.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class BrowseError(Exception):
    pass


@dataclass
class DirEntry:
    name: str
    is_dir: bool


def list_directory(root: Path, subpath: str) -> tuple[str, list[DirEntry]]:
    root = root.resolve()
    if not root.is_dir():
        raise BrowseError(f"{root} is not a real, accessible directory")

    target = (root / subpath).resolve()
    try:
        target.relative_to(root)
    except ValueError as e:
        raise BrowseError("subpath escapes the library root") from e
    if not target.is_dir():
        raise BrowseError(f"{subpath!r} not found under {root}")

    entries = sorted(
        (
            DirEntry(name=p.name, is_dir=p.is_dir())
            for p in target.iterdir()
            if not p.name.startswith(".")
        ),
        key=lambda e: (not e.is_dir, e.name.lower()),
    )
    return subpath, entries
