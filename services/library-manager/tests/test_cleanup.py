import os
import time
from pathlib import Path

from library_manager.cleanup import sweep_quarantine


def _make_quarantined_file(library_root: Path, source: str, name: str, age_days: float) -> Path:
    quarantine_dir = library_root / ".duplicates" / source
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    path = quarantine_dir / name
    path.write_bytes(b"fake audio")
    mtime = time.time() - age_days * 86400
    os.utime(path, (mtime, mtime))
    return path


def test_sweep_quarantine_no_directory_returns_empty(tmp_path: Path):
    assert sweep_quarantine(tmp_path / "library") == []


def test_sweep_quarantine_deletes_only_past_cutoff(tmp_path: Path):
    library_root = tmp_path / "library"
    old_file = _make_quarantined_file(library_root, "spotify", "old.mp3", age_days=20)
    fresh_file = _make_quarantined_file(library_root, "spotify", "fresh.mp3", age_days=1)

    removed = sweep_quarantine(library_root, older_than_days=14)

    assert removed == [old_file]
    assert not old_file.exists()
    assert fresh_file.exists()


def test_sweep_quarantine_dry_run_deletes_nothing(tmp_path: Path):
    library_root = tmp_path / "library"
    old_file = _make_quarantined_file(library_root, "spotify", "old.mp3", age_days=20)

    removed = sweep_quarantine(library_root, older_than_days=14, dry_run=True)

    assert removed == [old_file]
    assert old_file.exists()  # dry run — nothing actually deleted


def test_sweep_quarantine_respects_custom_cutoff(tmp_path: Path):
    library_root = tmp_path / "library"
    borderline = _make_quarantined_file(library_root, "spotify", "b.mp3", age_days=5)

    assert sweep_quarantine(library_root, older_than_days=14) == []
    assert sweep_quarantine(library_root, older_than_days=3) == [borderline]
