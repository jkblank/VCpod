from datetime import datetime, timedelta, timezone
from pathlib import Path

from common.activity import (
    ActivityEntry,
    get_last_sync,
    list_activity,
    prune_activity,
    record_activity,
    record_last_sync,
)

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _entry(**overrides) -> ActivityEntry:
    base = dict(
        started_at=NOW,
        service="fetch-scheduler",
        profile="john",
        description="fetch tick — 3 playlists",
        duration_seconds=12.5,
        result="ok",
    )
    base.update(overrides)
    return ActivityEntry(**base)


def test_list_activity_empty_when_nothing_recorded(tmp_path: Path):
    assert list_activity(tmp_path) == []


def test_record_and_list_round_trips(tmp_path: Path):
    record_activity(tmp_path, _entry())

    entries = list_activity(tmp_path)

    assert len(entries) == 1
    assert entries[0] == _entry()


def test_list_activity_newest_first(tmp_path: Path):
    record_activity(tmp_path, _entry(started_at=NOW - timedelta(hours=2), description="older"))
    record_activity(tmp_path, _entry(started_at=NOW, description="newer"))

    entries = list_activity(tmp_path)

    assert [e.description for e in entries] == ["newer", "older"]


def test_list_activity_respects_limit(tmp_path: Path):
    for i in range(5):
        record_activity(tmp_path, _entry(started_at=NOW - timedelta(hours=i), description=f"n{i}"))

    entries = list_activity(tmp_path, limit=2)

    assert len(entries) == 2
    assert entries[0].description == "n0"
    assert entries[1].description == "n1"


def test_prune_activity_deletes_only_older_entries(tmp_path: Path):
    record_activity(tmp_path, _entry(started_at=NOW - timedelta(days=40), description="ancient"))
    record_activity(tmp_path, _entry(started_at=NOW, description="recent"))

    deleted = prune_activity(tmp_path, older_than_days=30)

    assert deleted == 1
    remaining = list_activity(tmp_path)
    assert [e.description for e in remaining] == ["recent"]


def test_prune_activity_no_op_when_db_does_not_exist_yet(tmp_path: Path):
    assert prune_activity(tmp_path, older_than_days=30) == 0


def test_record_activity_multiple_writers_do_not_corrupt(tmp_path: Path):
    # Simulates fetch-scheduler and sync-orchestrator (two separate
    # processes in reality) both writing around the same tick --
    # sequential calls here, but exercises the same connect-per-call
    # pattern that has to tolerate a concurrent writer in practice.
    for i in range(10):
        record_activity(tmp_path, _entry(description=f"entry-{i}"))

    assert len(list_activity(tmp_path, limit=100)) == 10


# --- last-sync marker ------------------------------------------------------


def test_get_last_sync_none_when_never_recorded(tmp_path: Path):
    assert get_last_sync(tmp_path, "john") is None


def test_record_and_get_last_sync_round_trips(tmp_path: Path):
    record_last_sync(tmp_path, "john", NOW)

    assert get_last_sync(tmp_path, "john") == NOW


def test_last_sync_scoped_per_profile(tmp_path: Path):
    record_last_sync(tmp_path, "john", NOW)

    assert get_last_sync(tmp_path, "alice") is None


def test_get_last_sync_corrupt_file_returns_none_not_raise(tmp_path: Path):
    path = tmp_path / "john_last_sync.json"
    path.write_text("not json")

    assert get_last_sync(tmp_path, "john") is None
