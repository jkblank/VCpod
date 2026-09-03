"""The merge+tag orchestration shared by `audiobook-manager
import-audiobook` (cli.py) and web-gui-backend's discover-import route --
pulled out into its own function so the two never drift (same reasoning
common.config.resolve_config_path was moved out of music-stack-cli for:
a second real caller needing the exact same logic)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from audiobook_manager.beets_import import BeetsImportError, BeetsImportResult, import_audiobook
from audiobook_manager.discover import record_import
from audiobook_manager.merge import MergeError, merge_parts_to_m4b


class ImportPipelineError(Exception):
    """A real failure (ffmpeg/merge error, beets crashing) -- NOT
    beets-audible's own "couldn't confidently match" skip, which is a
    normal, expected outcome reported via ImportOutcome.imported=False
    instead of raised."""


@dataclass
class ImportOutcome:
    imported: bool
    imported_paths: list[Path]
    staging_dir: Path


def state_paths(state_root: Path | str) -> tuple[Path, Path]:
    """(beets_db_path, beets_config_dir), both under state_root/audiobooks/."""
    audiobooks_state = Path(state_root) / "audiobooks"
    return audiobooks_state / "beets-library.db", audiobooks_state / "beets-config"


def run_import_audiobook(
    parts_dir: Path | str,
    *,
    library_root: Path | str,
    state_root: Path | str,
    bitrate: str | None = None,
) -> ImportOutcome:
    """Merge a folder of raw parts into one staged .m4b, then tag+place it
    via beets-audible -- in one call. On success, records the import via
    discover.record_import so a later discover_audiobooks() scan sees
    this source folder as already processed, and removes the now-empty
    staging dir. Raises ImportPipelineError for a real failure; a
    beets-audible skip (couldn't confidently match) comes back as
    ImportOutcome(imported=False, ...), not an exception -- the caller
    decides how to surface the retry-with-metadata.yml instructions."""
    parts_dir = Path(parts_dir).resolve()
    library_root = Path(library_root).resolve()
    state_root = Path(state_root).resolve()
    beets_db_path, beets_config_dir = state_paths(state_root)

    staging_dir = state_root / "audiobooks" / "staging" / parts_dir.name
    staging_dir.mkdir(parents=True, exist_ok=True)
    merged_path = staging_dir / "merged.m4b"

    try:
        merge_parts_to_m4b(parts_dir, merged_path, bitrate=bitrate)
    except MergeError as e:
        raise ImportPipelineError(str(e)) from e

    try:
        result: BeetsImportResult = import_audiobook(
            staging_dir,
            audiobooks_root=library_root,
            beets_db_path=beets_db_path,
            beets_config_dir=beets_config_dir,
        )
    except BeetsImportError as e:
        raise ImportPipelineError(str(e)) from e

    if result.imported:
        record_import(state_root, parts_dir.name, result.imported_paths)
        try:
            staging_dir.rmdir()
        except OSError:
            pass  # not empty -- beets didn't move everything out, leave for inspection

    return ImportOutcome(
        imported=result.imported,
        imported_paths=result.imported_paths,
        staging_dir=staging_dir,
    )
