from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from audiobook_manager import beets_import


def test_build_beets_config_text_substitutes_absolute_paths(tmp_path: Path) -> None:
    audiobooks_root = tmp_path / "library" / "audiobooks"
    beets_db_path = tmp_path / "state" / "beets-library.db"

    text = beets_import.build_beets_config_text(
        audiobooks_root=audiobooks_root, beets_db_path=beets_db_path
    )

    assert f"directory: {audiobooks_root}" in text
    assert f"library: {beets_db_path}" in text
    assert "plugins: audible edit fromfilename scrub" in text
    assert "region: us" in text


def test_write_beets_config_creates_file(tmp_path: Path) -> None:
    config_dir = tmp_path / "beets-config"
    config_path = beets_import.write_beets_config(
        config_dir,
        audiobooks_root=tmp_path / "audiobooks",
        beets_db_path=tmp_path / "library.db",
    )

    assert config_path == config_dir / "config.yaml"
    assert config_path.is_file()


def test_import_audiobook_reports_success_when_new_item_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beets_db_path = tmp_path / "state" / "beets-library.db"
    source_dir = tmp_path / "staging"
    source_dir.mkdir(parents=True)
    fake_audio = source_dir / "merged.m4b"
    fake_audio.write_bytes(b"not real audio, just a placeholder")

    monkeypatch.setattr(beets_import, "find_beet", lambda: "beet")

    def fake_run(cmd, **kwargs):
        # Simulate what a real successful `beet import` would have done:
        # add one item to the library db pointing at fake_audio.
        from beets.library import Item, Library

        beets_db_path.parent.mkdir(parents=True, exist_ok=True)
        lib = Library(str(beets_db_path))
        item = Item(path=str(fake_audio).encode("utf-8"), title="The Trial")
        lib.add(item)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(beets_import.subprocess, "run", fake_run)

    result = beets_import.import_audiobook(
        source_dir,
        audiobooks_root=tmp_path / "library" / "audiobooks",
        beets_db_path=beets_db_path,
        beets_config_dir=tmp_path / "beets-config",
    )

    assert result.imported is True
    assert len(result.imported_paths) == 1
    assert result.imported_paths[0] == fake_audio


def test_import_audiobook_reports_skip_when_no_new_item(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    beets_db_path = tmp_path / "state" / "beets-library.db"
    source_dir = tmp_path / "staging"
    source_dir.mkdir(parents=True)

    monkeypatch.setattr(beets_import, "find_beet", lambda: "beet")

    def fake_run(cmd, **kwargs):
        # Simulate beet import -q skipping an unmatchable book: no db change.
        return subprocess.CompletedProcess(cmd, 0, stdout="Skipping.\n", stderr="")

    monkeypatch.setattr(beets_import.subprocess, "run", fake_run)

    result = beets_import.import_audiobook(
        source_dir,
        audiobooks_root=tmp_path / "library" / "audiobooks",
        beets_db_path=beets_db_path,
        beets_config_dir=tmp_path / "beets-config",
    )

    assert result.imported is False
    assert result.imported_paths == []


def test_import_audiobook_raises_on_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(beets_import, "find_beet", lambda: "beet")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    monkeypatch.setattr(beets_import.subprocess, "run", fake_run)

    with pytest.raises(beets_import.BeetsImportError):
        beets_import.import_audiobook(
            tmp_path / "staging",
            audiobooks_root=tmp_path / "library",
            beets_db_path=tmp_path / "library.db",
            beets_config_dir=tmp_path / "beets-config",
        )
