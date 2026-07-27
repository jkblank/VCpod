from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


class BeetsImportError(Exception):
    pass


_BEETS_CONFIG_TEMPLATE = """\
directory: {directory}
library: {library_db}
import:
  move: yes
plugins: audible edit fromfilename scrub
paths:
  "albumtype:audiobook series_name::.+ series_position::.+": $albumartist/%ifdef{{series_name}}/%ifdef{{series_position}} - $album%aunique{{}}/$track - $title
  "albumtype:audiobook series_name::.+": $albumartist/%ifdef{{series_name}}/$album%aunique{{}}/$track - $title
  "albumtype:audiobook": $albumartist/$album%aunique{{}}/$track - $title
  default: $albumartist/$album%aunique{{}}/$track - $title
musicbrainz:
  enabled: no
audible:
  match_chapters: true
  data_source_mismatch_penalty: 0.0
  fetch_art: true
  include_narrator_in_artists: true
  write_description_file: true
  write_reader_file: true
  region: us
"""


def find_beet() -> str:
    path = shutil.which("beet")
    if not path:
        raise BeetsImportError("beet not found on PATH (beets-audible not installed?)")
    return path


def build_beets_config_text(*, audiobooks_root: Path, beets_db_path: Path) -> str:
    return _BEETS_CONFIG_TEMPLATE.format(
        directory=audiobooks_root, library_db=beets_db_path
    )


def write_beets_config(
    config_dir: Path | str, *, audiobooks_root: Path, beets_db_path: Path
) -> Path:
    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "config.yaml"
    config_path.write_text(
        build_beets_config_text(
            audiobooks_root=audiobooks_root, beets_db_path=beets_db_path
        )
    )
    return config_path


@dataclass
class BeetsImportResult:
    imported: bool
    imported_paths: list[Path] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


def _existing_item_paths(beets_db_path: Path) -> dict[int, str]:
    if not beets_db_path.is_file():
        return {}
    from beets.library import Library

    lib = Library(str(beets_db_path))
    return {
        item.id: (
            item.path.decode("utf-8") if isinstance(item.path, bytes) else str(item.path)
        )
        for item in lib.items()
    }


def import_audiobook(
    source_dir: Path | str,
    *,
    audiobooks_root: Path | str,
    beets_db_path: Path | str,
    beets_config_dir: Path | str,
) -> BeetsImportResult:
    """Runs `beet import -q <source_dir>` against a project-managed,
    freshly-regenerated config (BEETSDIR-isolated from any real user beets
    install on the host), then verifies success by diffing
    beets.library.Library's item set before/after -- NOT by parsing
    beet's stdout, which isn't a stable contract (matches this codebase's
    convention of only checking subprocess returncode)."""
    beet_path = find_beet()
    source_dir = Path(source_dir).resolve()
    audiobooks_root = Path(audiobooks_root).resolve()
    beets_db_path = Path(beets_db_path).resolve()
    beets_config_dir = Path(beets_config_dir).resolve()

    config_path = write_beets_config(
        beets_config_dir, audiobooks_root=audiobooks_root, beets_db_path=beets_db_path
    )

    before = _existing_item_paths(beets_db_path)
    result = subprocess.run(
        [beet_path, "-c", str(config_path), "import", "-q", str(source_dir)],
        capture_output=True,
        text=True,
        env={**os.environ, "BEETSDIR": str(beets_config_dir)},
    )
    if result.returncode != 0:
        raise BeetsImportError(
            f"beet import exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )

    after = _existing_item_paths(beets_db_path)
    new_ids = after.keys() - before.keys()
    return BeetsImportResult(
        imported=bool(new_ids),
        imported_paths=[Path(after[i]) for i in new_ids],
        stdout=result.stdout,
        stderr=result.stderr,
    )


def verify_audiobook_classification(m4b_path: Path | str, ffprobe_path: str) -> list[str]:
    """Post-import sanity check closing the loop back to iOpenPod's
    stik==2 audiobook-classification rule. Non-fatal -- returns warning
    strings, doesn't raise."""
    from mutagen.mp4 import MP4

    m4b_path = Path(m4b_path)
    warnings: list[str] = []
    audio = MP4(str(m4b_path))
    if audio.get("stik") != [2]:
        warnings.append(
            f"stik atom is {audio.get('stik')!r}, not [2] -- "
            "iOpenPod may not classify this as an audiobook"
        )
    chapters = subprocess.run(
        [ffprobe_path, "-v", "error", "-show_chapters", "-of", "csv=p=0", str(m4b_path)],
        capture_output=True,
        text=True,
    )
    if not chapters.stdout.strip():
        warnings.append("no chapters found in final .m4b")
    return warnings
