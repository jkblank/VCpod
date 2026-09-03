from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.state import StateDB
from fetch_scheduler import loop as loop_module
from fetch_scheduler.loop import run_tick
from music_stack_cli.orchestrate import FetchAllResult

NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)

GLOBAL_YAML = """
paths:
  library_root: /data/library
  state_root: /data/state
sources:
  apple_music:
    enabled: true
    cookies_file: /config/secrets/apple.txt
  spotify:
    enabled: false
    credentials_file: /config/secrets/spotify.json
  ytmusic:
    enabled: true
    oauth_file: /config/secrets/yt_oauth.json
    cookies_file: /config/secrets/yt.txt
podcasts:
  pocketcasts:
    poll_interval_minutes: 60
"""


def _write_profile(config_root: Path, name: str, *, fetch_schedule: str | None, shows: str = "all"):
    fetch_block = f'fetch:\n  schedule: "{fetch_schedule}"\n' if fetch_schedule else ""
    (config_root / "profiles").mkdir(parents=True, exist_ok=True)
    (config_root / "profiles" / f"{name}.yaml").write_text(
        f"""
profile: {name}
device:
  match_by: volume_label
  match_value: "TEST"
{fetch_block}
playlists:
  - name: "Chill"
    source: apple_music
    source_id: "pl.1"
podcasts:
  pocketcasts:
    credentials_file: /config/secrets/pocketcasts/{name}.json
  sync_unplayed_only: true
  max_episodes_per_show: 5
  shows: {shows}
sync:
  trigger: manual
  transcode_format: alac
  push_play_status_back: false
"""
    )


def _setup(tmp_path: Path, *, profiles: dict[str, str | None]) -> Path:
    config_root = tmp_path / "config"
    (config_root / "global.yaml").parent.mkdir(parents=True, exist_ok=True)
    (config_root / "global.yaml").write_text(GLOBAL_YAML)
    for name, fetch_schedule in profiles.items():
        _write_profile(config_root, name, fetch_schedule=fetch_schedule)
    return config_root


def test_run_tick_calls_run_fetch_with_due_targets(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})  # always due (never fetched)
    captured = {}

    def fake_run_fetch(**kwargs):
        captured.update(kwargs)
        return FetchAllResult()

    monkeypatch.setattr(loop_module, "run_fetch", fake_run_fetch)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert captured["sources"] == {"apple_music", "podcasts"}
    assert captured["playlist_names"] == ["Chill"]
    assert captured["show_selectors"] is None  # "all" sentinel
    assert result.fetched["john"] == ["Chill", "__all__"]


def test_run_tick_records_fetch_completion_in_state_db(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())

    run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    with StateDB(tmp_path / "state" / "john.sqlite") as db:
        assert db.get_last_fetched("playlist", "Chill") == NOW
        assert db.get_last_fetched("podcast_show", "__all__") == NOW


def test_run_tick_does_not_record_target_whose_source_failed(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})

    def fake_run_fetch(**kwargs):
        result = FetchAllResult()
        result.source_errors.append("apple_music: could not authenticate (expired cookies)")
        return result

    monkeypatch.setattr(loop_module, "run_fetch", fake_run_fetch)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    with StateDB(tmp_path / "state" / "john.sqlite") as db:
        assert db.get_last_fetched("playlist", "Chill") is None  # left due, will retry
        assert db.get_last_fetched("podcast_show", "__all__") == NOW  # unaffected, still recorded
    assert result.fetched["john"] == ["__all__"]
    assert result.source_errors["john"] == [
        "apple_music: could not authenticate (expired cookies)"
    ]


def test_run_tick_skips_profile_with_nothing_due(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": None})  # no fetch schedule anywhere -> never due
    calls = []
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: calls.append(kwargs) or FetchAllResult())

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert calls == []
    assert result.fetched == {}


def test_run_tick_dry_run_does_not_call_run_fetch_or_write_state(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})
    calls = []
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: calls.append(kwargs) or FetchAllResult())

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
        dry_run=True,
    )

    assert calls == []
    assert result.fetched["john"] == ["Chill", "__all__"]
    with StateDB(tmp_path / "state" / "john.sqlite") as db:
        assert db.get_last_fetched("playlist", "Chill") is None


MAINTENANCE_GLOBAL_YAML = """
paths:
  library_root: /data/library
  state_root: /data/state
sources:
  apple_music:
    enabled: true
    cookies_file: /config/secrets/apple.txt
  spotify:
    enabled: false
    credentials_file: /config/secrets/spotify.json
  ytmusic:
    enabled: true
    oauth_file: /config/secrets/yt_oauth.json
    cookies_file: /config/secrets/yt.txt
podcasts:
  pocketcasts:
    poll_interval_minutes: 60
library_manager:
  dedup_enabled: {dedup_enabled}
  cleanup_enabled: {cleanup_enabled}
  normalize_artwork_enabled: {normalize_artwork_enabled}
backups:
  prune_enabled: {prune_enabled}
activity:
  prune_enabled: {activity_prune_enabled}
"""


def _setup_maintenance(
    tmp_path: Path,
    *,
    dedup_enabled: bool = False,
    cleanup_enabled: bool = False,
    normalize_artwork_enabled: bool = False,
    prune_enabled: bool = False,
    activity_prune_enabled: bool = False,
    profiles: dict[str, str | None] | None = None,
) -> Path:
    config_root = tmp_path / "config"
    (config_root / "global.yaml").parent.mkdir(parents=True, exist_ok=True)
    yaml_text = MAINTENANCE_GLOBAL_YAML.format(
        dedup_enabled=str(dedup_enabled).lower(),
        cleanup_enabled=str(cleanup_enabled).lower(),
        normalize_artwork_enabled=str(normalize_artwork_enabled).lower(),
        prune_enabled=str(prune_enabled).lower(),
        activity_prune_enabled=str(activity_prune_enabled).lower(),
    )
    (config_root / "global.yaml").write_text(yaml_text)

    for name, fetch_schedule in (profiles or {}).items():
        _write_profile(config_root, name, fetch_schedule=fetch_schedule)
    return config_root


class _FakeDedupResult:
    def __init__(self, quarantined_count: int):
        self.quarantined = [object()] * quarantined_count


def _fake_resolution():
    from common.backups import RetentionResolution

    return RetentionResolution(by_device_id={}, orphaned_device_ids=[])


def _fake_prune_result():
    from common.backups import PruneResult

    return PruneResult(
        deleted_snapshots={}, kept_snapshot_counts={}, deleted_blob_count=0,
        deleted_blob_bytes=0, dry_run=False,
    )


def test_run_tick_dedup_fires_when_enabled_and_a_fetch_happens(monkeypatch, tmp_path):
    # No schedule of its own — dedup runs as a post-step whenever any
    # profile actually fetches this tick, gated only by dedup_enabled.
    config_root = _setup_maintenance(
        tmp_path, dedup_enabled=True, profiles={"john": "* * * * *"}
    )
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state" / "global.sqlite").touch()  # must be excluded from state_db_paths

    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())
    monkeypatch.setattr(loop_module, "scan_library", lambda root: ["track1", "track2"])
    monkeypatch.setattr(loop_module, "find_duplicate_groups", lambda tracks, fuzzy_threshold: [["g1"]])
    captured = {}

    def fake_quarantine(groups, *, library_root, playlists_root, state_db_paths):
        captured["library_root"] = library_root
        captured["playlists_root"] = playlists_root
        captured["state_db_paths"] = state_db_paths
        return _FakeDedupResult(quarantined_count=1)

    monkeypatch.setattr(loop_module, "quarantine_duplicates", fake_quarantine)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert "library_dedup" in result.maintenance
    assert captured["library_root"] == tmp_path / "library" / "music"
    assert captured["playlists_root"] == tmp_path / "library" / "playlists"
    # john.sqlite is created for real by run_tick's own profile processing
    # above; global.sqlite was pre-touched here specifically to confirm
    # it's excluded from the glob passed to quarantine_duplicates.
    assert tmp_path / "state" / "john.sqlite" in captured["state_db_paths"]
    assert tmp_path / "state" / "global.sqlite" not in captured["state_db_paths"]


def test_run_tick_artwork_normalize_fires_when_enabled_and_a_fetch_happens(monkeypatch, tmp_path):
    # No schedule of its own, same as dedup/cleanup — runs as a post-step
    # whenever any profile actually fetches this tick, gated only by
    # normalize_artwork_enabled.
    config_root = _setup_maintenance(
        tmp_path, normalize_artwork_enabled=True, profiles={"john": "* * * * *"}
    )
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())

    class _FakeArtworkResult:
        scanned = 5
        normalized = [object(), object()]

    captured = {}

    def fake_normalize(library_root, *, dry_run=False):
        captured["library_root"] = library_root
        captured["dry_run"] = dry_run
        return _FakeArtworkResult()

    monkeypatch.setattr(loop_module, "normalize_library_artwork", fake_normalize)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert "artwork_normalize" in result.maintenance
    assert captured["library_root"] == tmp_path / "library" / "music"
    assert captured["dry_run"] is False
    assert "scanned 5" in result.maintenance["artwork_normalize"]
    assert "normalized 2" in result.maintenance["artwork_normalize"]


def test_run_tick_artwork_normalize_dry_run_does_not_write(monkeypatch, tmp_path):
    config_root = _setup_maintenance(
        tmp_path, normalize_artwork_enabled=True, profiles={"john": "* * * * *"}
    )

    class _FakeArtworkResult:
        scanned = 3
        normalized = [object()]

    captured = {}

    def fake_normalize(library_root, *, dry_run=False):
        captured["dry_run"] = dry_run
        return _FakeArtworkResult()

    monkeypatch.setattr(loop_module, "normalize_library_artwork", fake_normalize)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
        dry_run=True,
    )

    assert captured["dry_run"] is True
    assert "would normalize" in result.maintenance["artwork_normalize"]


def test_run_tick_maintenance_skipped_when_not_enabled(monkeypatch, tmp_path):
    config_root = _setup_maintenance(tmp_path, profiles={"john": "* * * * *"})  # all *_enabled default False
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())
    calls = []
    monkeypatch.setattr(loop_module, "scan_library", lambda root: calls.append(root) or [])

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert calls == []
    assert result.maintenance == {}


def test_run_tick_maintenance_skipped_when_nothing_fetched_even_if_enabled(monkeypatch, tmp_path):
    config_root = _setup_maintenance(
        tmp_path, dedup_enabled=True, profiles={"john": None}  # no fetch schedule -> never due
    )
    calls = []
    monkeypatch.setattr(loop_module, "scan_library", lambda root: calls.append(root) or [])

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert calls == []
    assert result.fetched == {}
    assert result.maintenance == {}


def test_run_tick_maintenance_task_exception_does_not_stop_others(monkeypatch, tmp_path):
    config_root = _setup_maintenance(
        tmp_path, dedup_enabled=True, prune_enabled=True, profiles={"john": "* * * * *"}
    )
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())

    def _raise(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(loop_module, "scan_library", _raise)
    monkeypatch.setattr(
        loop_module,
        "resolve_retention_map",
        lambda global_config, profiles, state_root: _fake_resolution(),
    )
    monkeypatch.setattr(loop_module, "prune_and_gc_backups", lambda *a, **k: _fake_prune_result())

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert "library_dedup" in result.maintenance_errors
    assert "backup_prune" in result.maintenance


def test_run_tick_dry_run_maintenance_does_not_call_mutating_function(monkeypatch, tmp_path):
    config_root = _setup_maintenance(
        tmp_path, dedup_enabled=True, profiles={"john": "* * * * *"}
    )
    monkeypatch.setattr(loop_module, "scan_library", lambda root: ["t1"])
    monkeypatch.setattr(loop_module, "find_duplicate_groups", lambda tracks, fuzzy_threshold: [["g1"]])
    calls = []
    monkeypatch.setattr(
        loop_module, "quarantine_duplicates", lambda *a, **k: calls.append(1) or _FakeDedupResult(0)
    )

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
        dry_run=True,
    )

    assert calls == []  # quarantine_duplicates never called under dry_run
    assert "would be quarantined" in result.maintenance["library_dedup"]


def test_run_tick_activity_prune_fires_when_enabled_and_a_fetch_happens(monkeypatch, tmp_path):
    from common.activity import ActivityEntry, record_activity

    config_root = _setup_maintenance(
        tmp_path, activity_prune_enabled=True, profiles={"john": "* * * * *"}
    )
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())
    state_root = tmp_path / "state"
    record_activity(
        state_root,
        ActivityEntry(
            started_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
            service="fetch-scheduler",
            profile="john",
            description="ancient",
            duration_seconds=1.0,
            result="ok",
        ),
    )

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=state_root,
        now=NOW,
    )

    assert "pruned 1" in result.maintenance["activity_prune"]


def test_run_tick_activity_prune_skipped_when_not_enabled(monkeypatch, tmp_path):
    config_root = _setup_maintenance(tmp_path, profiles={"john": "* * * * *"})
    monkeypatch.setattr(loop_module, "run_fetch", lambda **kwargs: FetchAllResult())

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert "activity_prune" not in result.maintenance


def test_run_tick_one_profile_exception_does_not_abort_the_rest(monkeypatch, tmp_path):
    config_root = _setup(
        tmp_path, profiles={"alice": "* * * * *", "bob": "* * * * *"}
    )

    def fake_run_fetch(**kwargs):
        if kwargs["profile"].profile == "alice":
            raise RuntimeError("boom")
        return FetchAllResult()

    monkeypatch.setattr(loop_module, "run_fetch", fake_run_fetch)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert result.errors == ["alice"]
    assert result.fetched["bob"] == ["Chill", "__all__"]
