from pathlib import Path

from common.models import PlaylistEntry

from music_stack_cli.orchestrate import resolve_config_path, resolve_roots, select_playlists


def test_resolve_config_path_rewrites_config_container_prefix(tmp_path):
    resolved = resolve_config_path("/config/secrets/apple_music_cookies.txt", tmp_path)
    assert resolved == tmp_path / "secrets" / "apple_music_cookies.txt"


def test_resolve_config_path_falls_back_to_literal_path_when_not_config_rooted(tmp_path):
    resolved = resolve_config_path("/somewhere/else/creds.json", tmp_path)
    assert resolved == Path("/somewhere/else/creds.json")


def test_resolve_roots_splits_library_root_into_music_playlists_podcasts(tmp_path):
    library_root = tmp_path / "library"
    state_root = tmp_path / "state"

    roots = resolve_roots(library_root, state_root, "john")

    assert roots.music_library_root == library_root / "music"
    assert roots.playlists_root == library_root / "playlists"
    assert roots.podcasts_library_root == library_root / "podcasts"
    assert roots.state_db_path == state_root / "john.sqlite"


def _entry(name: str, source: str) -> PlaylistEntry:
    return PlaylistEntry(name=name, source=source, source_id=f"id-{name}")


def test_select_playlists_filters_by_source():
    playlists = [_entry("Chill", "apple_music"), _entry("Semaphore", "ytmusic")]

    matched, unmatched = select_playlists(playlists, {"apple_music"}, None)

    assert [p.name for p in matched] == ["Chill"]
    assert unmatched == []


def test_select_playlists_filters_by_name_across_selected_sources():
    playlists = [
        _entry("Chill", "apple_music"),
        _entry("Semaphore", "ytmusic"),
        _entry("Elevate", "apple_music"),
    ]

    matched, unmatched = select_playlists(
        playlists, {"apple_music", "ytmusic"}, ["Chill", "Semaphore"]
    )

    assert {p.name for p in matched} == {"Chill", "Semaphore"}
    assert unmatched == []


def test_select_playlists_reports_unmatched_names():
    playlists = [_entry("Chill", "apple_music")]

    matched, unmatched = select_playlists(playlists, {"apple_music"}, ["Chill", "Nonexistent"])

    assert [p.name for p in matched] == ["Chill"]
    assert unmatched == ["Nonexistent"]


def test_select_playlists_name_filter_respects_source_restriction():
    # A playlist named "Chill" exists, but only under ytmusic is selected —
    # the apple_music one shouldn't match even though the name is right.
    playlists = [_entry("Chill", "apple_music")]

    matched, unmatched = select_playlists(playlists, {"ytmusic"}, ["Chill"])

    assert matched == []
    assert unmatched == ["Chill"]
