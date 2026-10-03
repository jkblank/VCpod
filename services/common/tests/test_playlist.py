from pathlib import Path

import pytest

from common.models import PlaylistEntry

from common.playlist import playlist_entry_path, prune_removed_playlists, write_m3u8


def test_write_m3u8_creates_parent_dirs_and_header(tmp_path: Path):
    target = tmp_path / "playlists" / "john" / "ALT CTRL.m3u8"
    write_m3u8(target, ["/library/music/A/B/01 Song.m4a", "/library/music/C/D/02 Song.m4a"])

    assert target.is_file()
    lines = target.read_text().splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1:] == [
        "/library/music/A/B/01 Song.m4a",
        "/library/music/C/D/02 Song.m4a",
    ]


def test_write_m3u8_empty_track_list(tmp_path: Path):
    target = tmp_path / "empty.m3u8"
    write_m3u8(target, [])
    assert target.read_text() == "#EXTM3U\n"


def test_write_m3u8_absolute_mode_replaces_contents(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"])

    # Source playlist dropped the first track and added a new one — an
    # "absolute" rewrite should mirror that exactly, including the removal.
    write_m3u8(target, ["/library/music/B/02 Song.m4a", "/library/music/C/03 Song.m4a"], mode="absolute")

    lines = target.read_text().splitlines()
    assert lines[1:] == [
        "/library/music/B/02 Song.m4a",
        "/library/music/C/03 Song.m4a",
    ]


def test_write_m3u8_additive_mode_preserves_existing_entries(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"])

    # Source playlist dropped the first track and added a new one — an
    # "additive" rewrite must keep the dropped track too, only adding the
    # genuinely new one.
    write_m3u8(target, ["/library/music/B/02 Song.m4a", "/library/music/C/03 Song.m4a"], mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == [
        "/library/music/A/01 Song.m4a",
        "/library/music/B/02 Song.m4a",
        "/library/music/C/03 Song.m4a",
    ]


def test_write_m3u8_additive_mode_no_duplicate_when_rerun_unchanged(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    tracks = ["/library/music/A/01 Song.m4a", "/library/music/B/02 Song.m4a"]
    write_m3u8(target, tracks, mode="additive")
    write_m3u8(target, tracks, mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == tracks


def test_write_m3u8_additive_mode_on_nonexistent_file_behaves_like_absolute(tmp_path: Path):
    target = tmp_path / "new_playlist.m3u8"
    write_m3u8(target, ["/library/music/A/01 Song.m4a"], mode="additive")

    lines = target.read_text().splitlines()
    assert lines[1:] == ["/library/music/A/01 Song.m4a"]


def test_write_m3u8_unknown_mode_raises(tmp_path: Path):
    target = tmp_path / "playlist.m3u8"
    with pytest.raises(ValueError):
        write_m3u8(target, ["/library/music/A/01 Song.m4a"], mode="bogus")


def _playlist_entry(name: str) -> PlaylistEntry:
    return PlaylistEntry(name=name, source="apple_music", source_id=f"pl.{name}")


def test_prune_removed_playlists_deletes_stale_file_not_in_current_list(tmp_path: Path):
    profile_dir = tmp_path / "nienie"
    profile_dir.mkdir(parents=True)
    write_m3u8(profile_dir / "Chill.m3u8", ["/library/music/A/01 Song.m4a"])
    write_m3u8(profile_dir / "Every1.m3u8", ["/library/music/B/02 Song.m4a"])

    pruned = prune_removed_playlists(
        [_playlist_entry("Chill")], playlists_root=tmp_path, profile_name="nienie"
    )

    assert pruned == ["Every1"]
    assert (profile_dir / "Chill.m3u8").is_file()
    assert not (profile_dir / "Every1.m3u8").exists()


def test_prune_removed_playlists_keeps_everything_when_all_still_configured(tmp_path: Path):
    profile_dir = tmp_path / "john"
    profile_dir.mkdir(parents=True)
    write_m3u8(profile_dir / "Chill.m3u8", [])
    write_m3u8(profile_dir / "Workout.m3u8", [])

    pruned = prune_removed_playlists(
        [_playlist_entry("Chill"), _playlist_entry("Workout")],
        playlists_root=tmp_path,
        profile_name="john",
    )

    assert pruned == []
    assert (profile_dir / "Chill.m3u8").is_file()
    assert (profile_dir / "Workout.m3u8").is_file()


def test_prune_removed_playlists_missing_profile_dir_returns_nothing(tmp_path: Path):
    pruned = prune_removed_playlists(
        [_playlist_entry("Chill")], playlists_root=tmp_path, profile_name="nobody"
    )
    assert pruned == []


def test_playlist_entry_path_unchanged_without_host_root(monkeypatch, tmp_path):
    monkeypatch.delenv("HOST_LIBRARY_ROOT", raising=False)
    track = tmp_path / "library" / "music" / "Artist" / "Album" / "01.m4a"

    assert playlist_entry_path(track, tmp_path / "library") == str(track)


def test_playlist_entry_path_rewrites_container_root_to_host_root(monkeypatch, tmp_path):
    # Regression: containerized fetches wrote /data/library/... entries that
    # the bare-metal device sync could never resolve -- every playlist track
    # silently dropped out of a real sync. See notes.md.
    monkeypatch.setenv("HOST_LIBRARY_ROOT", "/mnt/storage/vcpod/library")

    result = playlist_entry_path(
        "/data/library/music/Artist/Album/01.m4a", "/data/library"
    )

    assert result == "/mnt/storage/vcpod/library/music/Artist/Album/01.m4a"


def test_playlist_entry_path_leaves_paths_outside_library_root_alone(monkeypatch):
    monkeypatch.setenv("HOST_LIBRARY_ROOT", "/mnt/storage/vcpod/library")

    assert playlist_entry_path("/elsewhere/01.m4a", "/data/library") == "/elsewhere/01.m4a"


def test_write_m3u8_additive_normalizes_existing_container_entries(monkeypatch, tmp_path):
    monkeypatch.setenv("HOST_LIBRARY_ROOT", "/mnt/storage/vcpod/library")
    target = tmp_path / "Chill.m3u8"
    target.write_text("#EXTM3U\n/data/library/music/A/01.m4a\n")

    write_m3u8(
        target,
        ["/mnt/storage/vcpod/library/music/B/02.m4a"],
        mode="additive",
        library_root="/data/library",
    )

    entries = [l for l in target.read_text().splitlines() if l and not l.startswith("#")]
    assert entries == [
        "/mnt/storage/vcpod/library/music/A/01.m4a",
        "/mnt/storage/vcpod/library/music/B/02.m4a",
    ]
