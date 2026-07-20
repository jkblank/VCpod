from pathlib import Path

from common.state import EpisodeRecord, StateDB, TrackRecord


def _record(source_id: str = "123") -> TrackRecord:
    return TrackRecord(
        source="apple_music",
        source_id=source_id,
        local_path="/data/library/music/Artist/Album/01 Title.m4a",
        title="Title",
        artist="Artist",
        downloaded_at="2026-07-18T00:00:00+00:00",
    )


def _episode(episode_uuid: str = "ep-123") -> EpisodeRecord:
    return EpisodeRecord(
        episode_uuid=episode_uuid,
        podcast_uuid="show-1",
        show_name="Test Show",
        local_path="/data/library/podcasts/Test Show/Episode One.mp3",
        played=False,
        played_up_to=0,
        downloaded_at="2026-07-18T00:00:00+00:00",
    )


def test_unknown_track_returns_none(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        assert db.get_track("apple_music", "does-not-exist") is None


def test_record_and_get_round_trip(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        record = _record()
        db.record_track(record)
        fetched = db.get_track("apple_music", "123")
        assert fetched == record


def test_record_track_upserts_on_duplicate_key(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_track(_record())
        updated = _record()
        updated.local_path = "/data/library/music/Artist/Album/01 Title (new).m4a"
        db.record_track(updated)

        fetched = db.get_track("apple_music", "123")
        assert fetched.local_path == updated.local_path


def test_state_persists_across_connections(tmp_path: Path):
    path = tmp_path / "state.sqlite"
    with StateDB(path) as db:
        db.record_track(_record())

    with StateDB(path) as db:
        assert db.get_track("apple_music", "123") is not None


def test_update_local_path_updates_existing_row(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_track(_record())

        updated = db.update_local_path("apple_music", "123", "/library/music/.duplicates/apple_music/123.m4a")

        assert updated is True
        assert db.get_track("apple_music", "123").local_path == (
            "/library/music/.duplicates/apple_music/123.m4a"
        )


def test_update_local_path_no_op_for_unknown_track(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        updated = db.update_local_path("apple_music", "does-not-exist", "/some/path.m4a")
        assert updated is False


def test_unknown_episode_returns_none(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        assert db.get_episode("does-not-exist") is None


def test_record_and_get_episode_round_trip(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        episode = _episode()
        db.record_episode(episode)
        fetched = db.get_episode("ep-123")
        assert fetched == episode


def test_record_episode_upserts_on_duplicate_key(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode())
        updated = _episode()
        updated.played = True
        updated.played_up_to = 1234
        db.record_episode(updated)

        fetched = db.get_episode("ep-123")
        assert fetched.played is True
        assert fetched.played_up_to == 1234


def test_tracks_and_episodes_are_independent_tables(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_track(_record())
        db.record_episode(_episode())

        assert db.get_track("apple_music", "123") is not None
        assert db.get_episode("ep-123") is not None


def test_list_episodes_returns_all_rows(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode("ep-1"))
        db.record_episode(_episode("ep-2"))

        episodes = db.list_episodes()

        assert {e.episode_uuid for e in episodes} == {"ep-1", "ep-2"}


def test_list_episodes_empty_when_no_rows(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        assert db.list_episodes() == []


def test_episode_record_round_trips_title_audio_url_and_duration(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        episode = _episode()
        episode.title = "Episode One"
        episode.audio_url = "https://cdn.example/ep-123.mp3"
        episode.duration_seconds = 1800
        db.record_episode(episode)

        fetched = db.get_episode("ep-123")
        assert fetched.title == "Episode One"
        assert fetched.audio_url == "https://cdn.example/ep-123.mp3"
        assert fetched.duration_seconds == 1800


def test_episode_defaults_title_audio_url_duration_to_empty(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode())
        fetched = db.get_episode("ep-123")
        assert fetched.title == ""
        assert fetched.audio_url == ""
        assert fetched.duration_seconds == 0
        assert fetched.pending_push is False


def test_update_play_state_sets_pending_push_on_real_change(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode())

        updated = db.update_play_state("ep-123", played=True, played_up_to=900)

        assert updated is True
        fetched = db.get_episode("ep-123")
        assert fetched.played is True
        assert fetched.played_up_to == 900
        assert fetched.pending_push is True


def test_update_play_state_no_op_when_unchanged(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        episode = _episode()
        episode.played = True
        episode.played_up_to = 900
        db.record_episode(episode)

        db.update_play_state("ep-123", played=True, played_up_to=900)

        assert db.get_episode("ep-123").pending_push is False


def test_update_play_state_returns_false_for_unknown_episode(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        updated = db.update_play_state("does-not-exist", played=True, played_up_to=100)
        assert updated is False


def test_list_episodes_pending_push_only_returns_flagged_rows(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode("ep-1"))
        db.record_episode(_episode("ep-2"))
        db.update_play_state("ep-1", played=True, played_up_to=500)

        pending = db.list_episodes_pending_push()

        assert [e.episode_uuid for e in pending] == ["ep-1"]


def test_clear_pending_push_removes_flag(tmp_path: Path):
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode())
        db.update_play_state("ep-123", played=True, played_up_to=500)

        db.clear_pending_push("ep-123")

        assert db.get_episode("ep-123").pending_push is False
        assert db.list_episodes_pending_push() == []


def test_record_episode_does_not_reset_pending_push(tmp_path: Path):
    # A podcast-manager re-sync (record_episode's own upsert) must not
    # silently clobber a pending_push flag set by sync-orchestrator's
    # device read-back in between.
    with StateDB(tmp_path / "state.sqlite") as db:
        db.record_episode(_episode())
        db.update_play_state("ep-123", played=True, played_up_to=500)

        db.record_episode(_episode())

        assert db.get_episode("ep-123").pending_push is True


def test_pre_existing_episodes_table_migrates_new_columns_in_place(tmp_path: Path):
    # Simulates a real state.sqlite created before title/audio_url/
    # duration_seconds existed (e.g. the ones already in production) —
    # opening it with the current StateDB must add the new columns without
    # losing existing data, not fail or silently ignore old rows.
    import sqlite3

    path = tmp_path / "state.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE episodes (
            episode_uuid TEXT NOT NULL,
            podcast_uuid TEXT NOT NULL,
            show_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            played INTEGER NOT NULL,
            played_up_to INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            PRIMARY KEY (episode_uuid)
        )
        """
    )
    conn.execute(
        "INSERT INTO episodes VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("ep-old", "show-1", "Test Show", "/data/old.mp3", 0, 0, "2026-06-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    with StateDB(path) as db:
        fetched = db.get_episode("ep-old")
        assert fetched is not None
        assert fetched.local_path == "/data/old.mp3"
        assert fetched.title == ""
        assert fetched.audio_url == ""
        assert fetched.duration_seconds == 0
        assert fetched.pending_push is False
