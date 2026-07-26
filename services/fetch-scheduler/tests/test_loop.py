from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from common.state import StateDB
from fetch_scheduler import loop as loop_module
from fetch_scheduler.loop import run_tick
from music_stack_cli.orchestrate import SyncAllResult

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


def test_run_tick_calls_run_sync_with_due_targets(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})  # always due (never fetched)
    captured = {}

    def fake_run_sync(**kwargs):
        captured.update(kwargs)
        return SyncAllResult()

    monkeypatch.setattr(loop_module, "run_sync", fake_run_sync)

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
    monkeypatch.setattr(loop_module, "run_sync", lambda **kwargs: SyncAllResult())

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

    def fake_run_sync(**kwargs):
        result = SyncAllResult()
        result.source_errors.append("apple_music: could not authenticate (expired cookies)")
        return result

    monkeypatch.setattr(loop_module, "run_sync", fake_run_sync)

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
    monkeypatch.setattr(loop_module, "run_sync", lambda **kwargs: calls.append(kwargs) or SyncAllResult())

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert calls == []
    assert result.fetched == {}


def test_run_tick_dry_run_does_not_call_run_sync_or_write_state(monkeypatch, tmp_path):
    config_root = _setup(tmp_path, profiles={"john": "* * * * *"})
    calls = []
    monkeypatch.setattr(loop_module, "run_sync", lambda **kwargs: calls.append(kwargs) or SyncAllResult())

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


def test_run_tick_one_profile_exception_does_not_abort_the_rest(monkeypatch, tmp_path):
    config_root = _setup(
        tmp_path, profiles={"alice": "* * * * *", "bob": "* * * * *"}
    )

    def fake_run_sync(**kwargs):
        if kwargs["profile"].profile == "alice":
            raise RuntimeError("boom")
        return SyncAllResult()

    monkeypatch.setattr(loop_module, "run_sync", fake_run_sync)

    result = run_tick(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        now=NOW,
    )

    assert result.errors == ["alice"]
    assert result.fetched["bob"] == ["Chill", "__all__"]
