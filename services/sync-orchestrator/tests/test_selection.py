from pathlib import Path

from sync_orchestrator.selection import build_staging_dir, resolve_selected_files


def _make_library(tmp_path: Path) -> Path:
    library = tmp_path / "MusicLibrary"
    tracks = [
        "Linkin Park/Hybrid Theory/01 Papercut.m4a",
        "Linkin Park/Hybrid Theory/02 One Step Closer.m4a",
        "Linkin Park/Meteora/01 Foreword.m4a",
        "Alanis Morissette/Jagged Little Pill/01 All I Really Want.m4a",
        "The Cure/Disintegration/01 Plainsong.m4a",
    ]
    for rel in tracks:
        path = library / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake audio")
    return library


def test_resolve_include_mode_artist_level(tmp_path):
    library = _make_library(tmp_path)
    selected, unresolved = resolve_selected_files(library, ["Linkin Park"], mode="include")
    assert unresolved == []
    assert {p.relative_to(library).as_posix() for p in selected} == {
        "Linkin Park/Hybrid Theory/01 Papercut.m4a",
        "Linkin Park/Hybrid Theory/02 One Step Closer.m4a",
        "Linkin Park/Meteora/01 Foreword.m4a",
    }


def test_resolve_include_mode_album_level(tmp_path):
    library = _make_library(tmp_path)
    selected, _ = resolve_selected_files(
        library, ["Linkin Park/Hybrid Theory"], mode="include"
    )
    assert {p.relative_to(library).as_posix() for p in selected} == {
        "Linkin Park/Hybrid Theory/01 Papercut.m4a",
        "Linkin Park/Hybrid Theory/02 One Step Closer.m4a",
    }


def test_resolve_include_mode_track_level(tmp_path):
    library = _make_library(tmp_path)
    selected, _ = resolve_selected_files(
        library, ["Linkin Park/Hybrid Theory/01 Papercut.m4a"], mode="include"
    )
    assert {p.relative_to(library).as_posix() for p in selected} == {
        "Linkin Park/Hybrid Theory/01 Papercut.m4a",
    }


def test_resolve_include_overlapping_selections_no_duplicates(tmp_path):
    library = _make_library(tmp_path)
    selected, _ = resolve_selected_files(
        library, ["Linkin Park", "Linkin Park/Hybrid Theory"], mode="include"
    )
    rel_paths = [p.relative_to(library).as_posix() for p in selected]
    assert len(rel_paths) == len(set(rel_paths)) == 3


def test_resolve_exclude_mode_drops_one_artist(tmp_path):
    library = _make_library(tmp_path)
    selected, unresolved = resolve_selected_files(
        library, ["Alanis Morissette"], mode="exclude"
    )
    assert unresolved == []
    rel_paths = {p.relative_to(library).as_posix() for p in selected}
    assert "Alanis Morissette/Jagged Little Pill/01 All I Really Want.m4a" not in rel_paths
    assert "The Cure/Disintegration/01 Plainsong.m4a" in rel_paths
    assert "Linkin Park/Meteora/01 Foreword.m4a" in rel_paths


def test_resolve_unresolved_selection_is_reported(tmp_path):
    library = _make_library(tmp_path)
    _, unresolved = resolve_selected_files(
        library, ["Radiohead", "Linkin Park"], mode="include"
    )
    assert unresolved == ["Radiohead"]


def test_resolve_empty_selections_include_means_nothing(tmp_path):
    library = _make_library(tmp_path)
    selected, unresolved = resolve_selected_files(library, [], mode="include")
    assert selected == []
    assert unresolved == []


def test_resolve_empty_selections_exclude_means_everything(tmp_path):
    library = _make_library(tmp_path)
    selected, _ = resolve_selected_files(library, [], mode="exclude")
    assert len(selected) == 5


def test_build_staging_dir_creates_symlinks_to_real_files(tmp_path):
    library = _make_library(tmp_path)
    staging = tmp_path / "staging"
    selected, _ = resolve_selected_files(library, ["Linkin Park/Meteora"], mode="include")

    build_staging_dir(staging, library, selected)

    staged = staging / "Linkin Park" / "Meteora" / "01 Foreword.m4a"
    assert staged.is_symlink()
    assert staged.read_bytes() == b"fake audio"
    assert not (staging / "The Cure").exists()


def test_build_staging_dir_rebuild_drops_deselected_files(tmp_path):
    library = _make_library(tmp_path)
    staging = tmp_path / "staging"

    selected, _ = resolve_selected_files(library, ["Linkin Park"], mode="include")
    build_staging_dir(staging, library, selected)
    assert (staging / "Linkin Park" / "Meteora" / "01 Foreword.m4a").exists()

    narrower, _ = resolve_selected_files(
        library, ["Linkin Park/Hybrid Theory"], mode="include"
    )
    build_staging_dir(staging, library, narrower)

    assert not (staging / "Linkin Park" / "Meteora").exists()
    assert (staging / "Linkin Park" / "Hybrid Theory" / "01 Papercut.m4a").exists()
