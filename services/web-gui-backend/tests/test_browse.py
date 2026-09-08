import pytest

from web_gui_backend.browse import BrowseError, list_directory


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "Talking Heads" / "Remain in Light").mkdir(parents=True)
    (tmp_path / "Talking Heads" / "Remain in Light" / "01 Born Under Punches.m4a").touch()
    (tmp_path / "Linkin Park").mkdir()
    (tmp_path / ".hidden").mkdir()
    return tmp_path


def test_list_directory_lists_root_entries(tree):
    subpath, entries = list_directory(tree, "")

    assert subpath == ""
    names = {e.name for e in entries}
    assert names == {"Talking Heads", "Linkin Park"}


def test_list_directory_hides_dotfiles(tree):
    _, entries = list_directory(tree, "")
    assert ".hidden" not in {e.name for e in entries}


def test_list_directory_dirs_sort_before_files_then_alphabetically(tree):
    (tree / "Talking Heads" / "Remain in Light" / "02 Crosseyed.m4a").touch()
    _, entries = list_directory(tree / "Talking Heads" / "Remain in Light", "")

    assert [e.name for e in entries] == [
        "01 Born Under Punches.m4a",
        "02 Crosseyed.m4a",
    ]


def test_list_directory_descends_into_subpath(tree):
    subpath, entries = list_directory(tree, "Talking Heads")

    assert subpath == "Talking Heads"
    assert [e.name for e in entries] == ["Remain in Light"]
    assert entries[0].is_dir is True


def test_list_directory_rejects_escaping_subpath(tree):
    with pytest.raises(BrowseError, match="escapes"):
        list_directory(tree, "../../etc")


def test_list_directory_rejects_nonexistent_root(tmp_path):
    with pytest.raises(BrowseError, match="not a real"):
        list_directory(tmp_path / "does-not-exist", "")


def test_list_directory_rejects_nonexistent_subpath(tree):
    with pytest.raises(BrowseError, match="not found"):
        list_directory(tree, "Nonexistent Artist")


def test_list_directory_rejects_subpath_that_is_a_file_not_a_dir(tree):
    with pytest.raises(BrowseError):
        list_directory(tree, "Talking Heads/Remain in Light/01 Born Under Punches.m4a")
