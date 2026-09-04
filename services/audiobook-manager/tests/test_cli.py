from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from audiobook_manager import cli, pipeline
from audiobook_manager.beets_import import BeetsImportResult
from audiobook_manager.merge import MergeError


def test_cmd_merge_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    output = tmp_path / "out.m4b"
    monkeypatch.setattr(cli, "merge_parts_to_m4b", lambda parts_dir, out, bitrate: output)

    args = argparse.Namespace(parts_dir=str(tmp_path), output=str(output), bitrate="64k")
    assert cli._cmd_merge(args) == 0
    assert "Merged into" in capsys.readouterr().out


def test_cmd_merge_reports_merge_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    def boom(parts_dir, out, bitrate):
        raise MergeError("no ffmpeg")

    monkeypatch.setattr(cli, "merge_parts_to_m4b", boom)

    args = argparse.Namespace(parts_dir=str(tmp_path), output="out.m4b", bitrate="64k")
    assert cli._cmd_merge(args) == 1
    assert "no ffmpeg" in capsys.readouterr().out


def test_cmd_tag_success_prints_imported_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    imported_path = tmp_path / "library" / "audiobooks" / "Kafka" / "The Trial.m4b"
    monkeypatch.setattr(
        cli,
        "import_audiobook",
        lambda *a, **k: BeetsImportResult(imported=True, imported_paths=[imported_path]),
    )
    monkeypatch.setattr(cli, "verify_audiobook_classification", lambda *a, **k: [])
    monkeypatch.setattr(cli, "find_ffprobe", lambda: "ffprobe")

    args = argparse.Namespace(
        source_dir=str(tmp_path / "staging"),
        library_root=str(tmp_path / "library" / "audiobooks"),
        state_root=str(tmp_path / "state"),
    )
    assert cli._cmd_tag(args) == 0
    out = capsys.readouterr().out
    assert "Imported 1 file(s)" in out
    assert str(imported_path) in out


def test_cmd_tag_skip_prints_loud_error_and_retry_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.setattr(
        cli, "import_audiobook", lambda *a, **k: BeetsImportResult(imported=False)
    )

    source_dir = tmp_path / "staging"
    args = argparse.Namespace(
        source_dir=str(source_dir),
        library_root=str(tmp_path / "library" / "audiobooks"),
        state_root=str(tmp_path / "state"),
    )
    assert cli._cmd_tag(args) == 1
    out = capsys.readouterr().out
    assert "could not confidently match" in out
    assert "audiobook-manager tag --source-dir" in out


def test_cmd_import_audiobook_success_removes_empty_staging_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    parts_dir = tmp_path / "Franz Kafka - The Trial"
    parts_dir.mkdir()
    state_root = tmp_path / "state"
    library_root = tmp_path / "library" / "audiobooks"

    monkeypatch.setattr(pipeline, "merge_parts_to_m4b", lambda parts_dir, out, bitrate: out)

    imported_path = library_root / "Kafka" / "The Trial.m4b"

    def fake_import(source_dir, **kwargs):
        # Simulate beets moving the staged file out, leaving the dir empty.
        Path(source_dir, "merged.m4b").unlink(missing_ok=True)
        return BeetsImportResult(imported=True, imported_paths=[imported_path])

    monkeypatch.setattr(pipeline, "import_audiobook", fake_import)
    monkeypatch.setattr(cli, "verify_audiobook_classification", lambda *a, **k: [])
    monkeypatch.setattr(cli, "find_ffprobe", lambda: "ffprobe")

    args = argparse.Namespace(
        parts_dir=str(parts_dir),
        library_root=str(library_root),
        state_root=str(state_root),
        bitrate="64k",
    )
    assert cli._cmd_import_audiobook(args) == 0

    staging_dir = state_root / "audiobooks" / "staging" / parts_dir.name
    assert not staging_dir.exists()


def test_cmd_import_audiobook_skip_leaves_staging_dir_and_reports_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    parts_dir = tmp_path / "Franz Kafka - The Trial"
    parts_dir.mkdir()
    state_root = tmp_path / "state"
    library_root = tmp_path / "library" / "audiobooks"

    monkeypatch.setattr(pipeline, "merge_parts_to_m4b", lambda parts_dir, out, bitrate: out)
    monkeypatch.setattr(
        pipeline, "import_audiobook", lambda *a, **k: BeetsImportResult(imported=False)
    )

    args = argparse.Namespace(
        parts_dir=str(parts_dir),
        library_root=str(library_root),
        state_root=str(state_root),
        bitrate="64k",
    )
    assert cli._cmd_import_audiobook(args) == 1

    staging_dir = state_root / "audiobooks" / "staging" / parts_dir.name
    assert staging_dir.exists()
    out = capsys.readouterr().out
    assert "could not confidently match" in out
    assert str(staging_dir) in out


def test_cmd_discover_prints_new_and_already_imported_status(tmp_path, capsys):
    root = tmp_path / "drop-zone"
    (root / "Franz Kafka - The Trial").mkdir(parents=True)
    (root / "Franz Kafka - The Trial" / "01.mp3").write_bytes(b"")
    (root / "George Orwell - 1984").mkdir(parents=True)
    (root / "George Orwell - 1984" / "01.mp3").write_bytes(b"")
    state_root = tmp_path / "state"

    from audiobook_manager.discover import record_import

    record_import(state_root, "Franz Kafka - The Trial", [tmp_path / "lib" / "trial.m4b"])

    args = argparse.Namespace(root=str(root), state_root=str(state_root))
    assert cli._cmd_discover(args) == 0

    out = capsys.readouterr().out
    assert "Franz Kafka - The Trial" in out
    assert "already imported" in out
    assert "George Orwell - 1984" in out
    assert "NEW -- needs processing" in out


def test_cmd_discover_reports_when_nothing_found(tmp_path, capsys):
    args = argparse.Namespace(root=str(tmp_path / "empty"), state_root=str(tmp_path / "state"))
    assert cli._cmd_discover(args) == 0
    assert "No audiobook candidates found" in capsys.readouterr().out


def test_cmd_tag_success_records_import_for_discover(tmp_path, monkeypatch):
    imported_path = tmp_path / "library" / "audiobooks" / "Kafka" / "The Trial.m4b"
    monkeypatch.setattr(
        cli,
        "import_audiobook",
        lambda *a, **k: BeetsImportResult(imported=True, imported_paths=[imported_path]),
    )
    monkeypatch.setattr(cli, "verify_audiobook_classification", lambda *a, **k: [])
    monkeypatch.setattr(cli, "find_ffprobe", lambda: "ffprobe")

    source_dir = tmp_path / "Franz Kafka - The Trial"
    state_root = tmp_path / "state"
    args = argparse.Namespace(
        source_dir=str(source_dir),
        library_root=str(tmp_path / "library" / "audiobooks"),
        state_root=str(state_root),
    )
    assert cli._cmd_tag(args) == 0

    from audiobook_manager.discover import discover_audiobooks

    root = tmp_path
    (root / "Franz Kafka - The Trial").mkdir(exist_ok=True)
    (root / "Franz Kafka - The Trial" / "01.mp3").write_bytes(b"")
    books = discover_audiobooks(root, state_root)
    assert books[0].already_imported is True
    assert books[0].library_paths == [str(imported_path)]


def test_cmd_import_audiobook_success_records_import_for_discover(tmp_path, monkeypatch):
    parts_dir = tmp_path / "Franz Kafka - The Trial"
    parts_dir.mkdir()
    (parts_dir / "01.mp3").write_bytes(b"")
    state_root = tmp_path / "state"
    library_root = tmp_path / "library" / "audiobooks"

    monkeypatch.setattr(pipeline, "merge_parts_to_m4b", lambda parts_dir, out, bitrate: out)

    imported_path = library_root / "Kafka" / "The Trial.m4b"

    def fake_import(source_dir, **kwargs):
        Path(source_dir, "merged.m4b").unlink(missing_ok=True)
        return BeetsImportResult(imported=True, imported_paths=[imported_path])

    monkeypatch.setattr(pipeline, "import_audiobook", fake_import)
    monkeypatch.setattr(cli, "verify_audiobook_classification", lambda *a, **k: [])
    monkeypatch.setattr(cli, "find_ffprobe", lambda: "ffprobe")

    args = argparse.Namespace(
        parts_dir=str(parts_dir),
        library_root=str(library_root),
        state_root=str(state_root),
        bitrate="64k",
    )
    assert cli._cmd_import_audiobook(args) == 0

    from audiobook_manager.discover import discover_audiobooks

    books = discover_audiobooks(tmp_path, state_root)
    trial = next(b for b in books if b.name == "Franz Kafka - The Trial")
    assert trial.already_imported is True
    assert trial.library_paths == [str(imported_path)]
