from datetime import datetime, timezone
from pathlib import Path

import pytest
from common.state import StateDB

from sync_orchestrator.music_index import (
    DECISION_ADOPT,
    DECISION_REMOVE,
    apply_device_decision,
    build_index_staging,
    playlist_track_keys,
    record_music_writes,
    under_music_root,
    untracked_tracks,
)

NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


def _make_music(root: Path, *keys: str) -> None:
    for key in keys:
        track = root / key
        track.parent.mkdir(parents=True, exist_ok=True)
        track.write_bytes(b"audio")


def test_untracked_tracks_is_matched_minus_index_and_playlists():
    matched = {"A/1.m4a", "B/2.m4a", "C/3.m4a", "D/4.m4a"}

    assert untracked_tracks(matched, {"A/1.m4a"}, {"B/2.m4a"}) == ["C/3.m4a", "D/4.m4a"]


def test_untracked_tracks_empty_when_everything_is_accounted_for():
    assert untracked_tracks({"A/1.m4a"}, {"A/1.m4a"}, set()) == []


def test_under_music_root_rejects_paths_outside_the_music_root(tmp_path):
    music = tmp_path / "music"

    assert under_music_root(str(music / "A/1.m4a"), music) == "A/1.m4a"
    assert under_music_root(str(tmp_path / "podcasts/show/ep.mp3"), music) is None


def test_playlist_track_keys_reads_staged_entries_relative_to_music_root(tmp_path):
    music = tmp_path / "music"
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "Rise Up.m3u8").write_text(
        f"#EXTM3U\n{music / 'A/1.m4a'}\n{music / 'B/2.m4a'}\n"
    )

    assert playlist_track_keys(staging, music) == {"A/1.m4a", "B/2.m4a"}


def test_build_index_staging_links_present_tracks_and_reports_missing(tmp_path):
    music = tmp_path / "music"
    _make_music(music, "A/1.m4a")
    staging = tmp_path / "staging"

    missing = build_index_staging(staging, music, {"A/1.m4a", "B/gone.m4a"})

    assert missing == ["B/gone.m4a"]
    assert (staging / "A" / "1.m4a").is_symlink()
    assert not (staging / "B").exists()


def test_build_index_staging_with_empty_index_is_an_empty_folder(tmp_path):
    music = tmp_path / "music"
    _make_music(music, "A/1.m4a")
    staging = tmp_path / "staging"

    assert build_index_staging(staging, music, set()) == []
    assert staging.is_dir()
    assert list(staging.rglob("*.m4a")) == []


def test_record_music_writes_adds_only_music_tracks(tmp_path):
    music = tmp_path / "music"
    with StateDB(tmp_path / "john.sqlite") as db:
        added = record_music_writes(
            db,
            [str(music / "A/1.m4a"), str(tmp_path / "podcasts/show/ep.mp3")],
            music,
            NOW,
        )
        assert added == 1
        assert db.index_tracks() == {"A/1.m4a"}


def test_adopt_keeps_the_pending_tracks_in_the_index(tmp_path):
    with StateDB(tmp_path / "john.sqlite") as db:
        db.set_pending_untracked("SER1", ["C/3.m4a", "D/4.m4a"])

        covered = apply_device_decision(db, "SER1", DECISION_ADOPT, NOW)

        assert covered == ["C/3.m4a", "D/4.m4a"]
        assert db.index_tracks() == {"C/3.m4a", "D/4.m4a"}
        assert db.get_device_decision("SER1") == DECISION_ADOPT
        assert db.pending_untracked("SER1") == []


def test_remove_leaves_the_index_untouched(tmp_path):
    with StateDB(tmp_path / "john.sqlite") as db:
        db.set_pending_untracked("SER1", ["C/3.m4a"])

        apply_device_decision(db, "SER1", DECISION_REMOVE, NOW)

        assert db.index_tracks() == set()
        assert db.get_device_decision("SER1") == DECISION_REMOVE


def test_decisions_are_per_device(tmp_path):
    with StateDB(tmp_path / "john.sqlite") as db:
        apply_device_decision(db, "SER1", DECISION_ADOPT, NOW)

        assert db.get_device_decision("SER1") == DECISION_ADOPT
        assert db.get_device_decision("SER2") is None


def test_unknown_decision_is_rejected(tmp_path):
    with StateDB(tmp_path / "john.sqlite") as db:
        with pytest.raises(ValueError, match="unknown decision"):
            apply_device_decision(db, "SER1", "keep-everything", NOW)
