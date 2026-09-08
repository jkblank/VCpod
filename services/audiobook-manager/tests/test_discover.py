from __future__ import annotations

from pathlib import Path

from audiobook_manager.discover import discover_audiobooks, record_import


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_discover_returns_empty_list_when_root_does_not_exist(tmp_path):
    books = discover_audiobooks(tmp_path / "no-such-root", tmp_path / "state")
    assert books == []


def test_discover_finds_folders_with_audio_files(tmp_path):
    root = tmp_path / "drop-zone"
    _touch(root / "Franz Kafka - The Trial" / "01.mp3")
    _touch(root / "Franz Kafka - The Trial" / "02.mp3")
    _touch(root / "George Orwell - 1984" / "01.mp3")

    books = discover_audiobooks(root, tmp_path / "state")

    names = {b.name for b in books}
    assert names == {"Franz Kafka - The Trial", "George Orwell - 1984"}
    trial = next(b for b in books if b.name == "Franz Kafka - The Trial")
    assert trial.audio_file_count == 2
    assert trial.already_imported is False
    assert trial.imported_at is None


def test_discover_ignores_folders_with_no_audio_files(tmp_path):
    root = tmp_path / "drop-zone"
    _touch(root / "just some notes" / "readme.txt")

    books = discover_audiobooks(root, tmp_path / "state")

    assert books == []


def test_discover_ignores_non_directory_entries(tmp_path):
    root = tmp_path / "drop-zone"
    root.mkdir()
    (root / "stray.mp3").write_bytes(b"")

    books = discover_audiobooks(root, tmp_path / "state")

    assert books == []


def test_discover_marks_previously_imported_books(tmp_path):
    root = tmp_path / "drop-zone"
    _touch(root / "Franz Kafka - The Trial" / "01.mp3")
    state_root = tmp_path / "state"

    imported_path = tmp_path / "library" / "Franz Kafka" / "The Trial" / "01.m4b"
    record_import(state_root, "Franz Kafka - The Trial", [imported_path])

    books = discover_audiobooks(root, state_root)

    assert len(books) == 1
    book = books[0]
    assert book.already_imported is True
    assert book.imported_at is not None
    assert book.library_paths == [str(imported_path)]


def test_discover_only_flags_the_matching_folder_by_name(tmp_path):
    root = tmp_path / "drop-zone"
    _touch(root / "Franz Kafka - The Trial" / "01.mp3")
    _touch(root / "George Orwell - 1984" / "01.mp3")
    state_root = tmp_path / "state"
    record_import(state_root, "Franz Kafka - The Trial", [Path("/lib/x.m4b")])

    books = discover_audiobooks(root, state_root)

    by_name = {b.name: b.already_imported for b in books}
    assert by_name == {"Franz Kafka - The Trial": True, "George Orwell - 1984": False}


def test_record_import_is_idempotent_and_overwrites_previous_entry(tmp_path):
    state_root = tmp_path / "state"
    record_import(state_root, "Some Book", [Path("/lib/old.m4b")])
    record_import(state_root, "Some Book", [Path("/lib/new.m4b")])

    root = tmp_path / "drop-zone"
    _touch(root / "Some Book" / "01.mp3")
    books = discover_audiobooks(root, state_root)

    assert books[0].library_paths == ["/lib/new.m4b"]
