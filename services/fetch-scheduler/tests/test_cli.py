from common.activity import list_activity
from fetch_scheduler.cli import _print_tick_result, _record_tick_activity
from fetch_scheduler.loop import TickResult


def test_print_tick_result_surfaces_source_errors(capsys):
    # Confirmed live: a target that's due every tick but always fails a
    # source auth check (e.g. expired cookies) printed nothing at all
    # before this — source_errors must be visible, not silently invisible
    # forever just because it's not an unexpected exception.
    result = TickResult(
        fetched={"alice": []},
        source_errors={"alice": ["apple_music: could not authenticate (expired cookies)"]},
    )

    _print_tick_result(result)

    captured = capsys.readouterr()
    assert "apple_music: could not authenticate (expired cookies)" in captured.out


def test_print_tick_result_nothing_due_message(capsys):
    _print_tick_result(TickResult())

    captured = capsys.readouterr()
    assert "nothing due" in captured.out


def test_print_tick_result_fetched_and_tick_error(capsys):
    result = TickResult(fetched={"alice": ["Chill"]}, errors=["bob"])

    _print_tick_result(result)

    captured = capsys.readouterr()
    assert "[alice] fetched: Chill" in captured.out
    assert "[bob] ERROR: tick failed, see log" in captured.out


# --- _record_tick_activity --------------------------------------------------


def test_record_tick_activity_nothing_due_writes_nothing(tmp_path):
    _record_tick_activity(tmp_path, TickResult(), duration_seconds=0.1)

    assert list_activity(tmp_path) == []


def test_record_tick_activity_skips_profiles_with_empty_fetch_list(tmp_path):
    # A profile can be present in result.fetched with an empty list (every
    # due target's source failed and was excluded, or nothing was due) --
    # that's not real activity and must not flood the log every tick.
    result = TickResult(fetched={"alice": []})

    _record_tick_activity(tmp_path, result, duration_seconds=0.1)

    assert list_activity(tmp_path) == []


def test_record_tick_activity_writes_ok_entry_for_real_fetch(tmp_path):
    result = TickResult(fetched={"alice": ["Chill", "Elevate"]})

    _record_tick_activity(tmp_path, result, duration_seconds=4.2)

    entries = list_activity(tmp_path)
    assert len(entries) == 1
    assert entries[0].profile == "alice"
    assert entries[0].service == "fetch-scheduler"
    assert entries[0].result == "ok"
    assert "Chill" in entries[0].description and "Elevate" in entries[0].description
    assert entries[0].duration_seconds == 4.2


def test_record_tick_activity_writes_error_entry_for_source_errors(tmp_path):
    result = TickResult(
        fetched={"alice": []},
        source_errors={"alice": ["apple_music: could not authenticate (expired cookies)"]},
    )

    _record_tick_activity(tmp_path, result, duration_seconds=1.0)

    entries = list_activity(tmp_path)
    assert len(entries) == 1
    assert entries[0].profile == "alice"
    assert entries[0].result == "error"
    assert "expired cookies" in entries[0].description


def test_record_tick_activity_writes_error_entry_for_unexpected_tick_failure(tmp_path):
    result = TickResult(errors=["bob"])

    _record_tick_activity(tmp_path, result, duration_seconds=1.0)

    entries = list_activity(tmp_path)
    assert len(entries) == 1
    assert entries[0].profile == "bob"
    assert entries[0].result == "error"


def test_record_tick_activity_writes_maintenance_entries(tmp_path):
    result = TickResult(
        fetched={"alice": ["Chill"]},
        maintenance={"library_dedup": "scanned 10 tracks, no cross-source duplicates found"},
        maintenance_errors={"backup_prune": "task failed, see log"},
    )

    _record_tick_activity(tmp_path, result, duration_seconds=2.0)

    entries = list_activity(tmp_path, limit=100)
    profiles = {e.profile for e in entries}
    assert "alice" in profiles
    assert "all" in profiles
    maintenance_entries = [e for e in entries if e.profile == "all"]
    assert len(maintenance_entries) == 2
    ok_entry = next(e for e in maintenance_entries if "library_dedup" in e.description)
    error_entry = next(e for e in maintenance_entries if "backup_prune" in e.description)
    assert ok_entry.result == "ok"
    assert error_entry.result == "error"
