from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.config import ConfigError
from common.state import StateDB
from sync_orchestrator import cli as cli_module
from sync_orchestrator.device import AmbiguousDeviceMatchError, DeviceNotFoundError

NOW = datetime(2026, 7, 25, 22, 0, tzinfo=timezone.utc)


def _write_profile(
    directory: Path, filename: str, profile_name: str, *, fetch_schedule: str | None = None
) -> Path:
    fetch_block = f'fetch:\n  schedule: "{fetch_schedule}"\n' if fetch_schedule else ""
    path = directory / filename
    path.write_text(
        f"""
profile: {profile_name}
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
    credentials_file: /config/secrets/pocketcasts/{profile_name}.json
  sync_unplayed_only: true
  max_episodes_per_show: 5
  shows: all
sync:
  trigger: manual
  transcode_format: alac
  push_play_status_back: false
"""
    )
    return path


# --- _load_profiles_with_paths -----------------------------------------------


def test_load_profiles_with_paths_returns_path_alongside_profile(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_profile(profiles_dir, "alice.yaml", "alice")
    _write_profile(profiles_dir, "bob.yaml", "bob")

    pairs = cli_module._load_profiles_with_paths(profiles_dir)

    by_name = {profile.profile: path for path, profile in pairs}
    assert by_name == {
        "alice": profiles_dir / "alice.yaml",
        "bob": profiles_dir / "bob.yaml",
    }


def test_load_profiles_with_paths_raises_on_duplicate_profile_name(tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    _write_profile(profiles_dir, "a.yaml", "john")
    _write_profile(profiles_dir, "b.yaml", "john")

    with pytest.raises(ConfigError, match="duplicate profile name"):
        cli_module._load_profiles_with_paths(profiles_dir)


# --- _maybe_pre_fetch ---------------------------------------------------------


def _args(**overrides) -> argparse.Namespace:
    base = dict(
        library_root="/library",
        state_root="/state",
        pre_fetch_horizon_hours=4.0,
        music_stack_project_dir="services/music-stack-cli",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_maybe_pre_fetch_skips_subprocess_when_nothing_due_soon(monkeypatch, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john")  # no fetch schedule anywhere
    from common.config import load_profile_config

    profile = load_profile_config(path)

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    cli_module._maybe_pre_fetch(
        _args(state_root=str(tmp_path / "state")), profile, path, tmp_path, NOW
    )

    assert calls == []


def test_maybe_pre_fetch_invokes_subprocess_with_expected_args_when_due_soon(
    monkeypatch, tmp_path
):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    # never fetched + a schedule => always "due within any horizon"
    path = _write_profile(profiles_dir, "john.yaml", "john", fetch_schedule="0 3 * * *")
    from common.config import load_profile_config

    profile = load_profile_config(path)

    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    state_root = tmp_path / "state"
    cli_module._maybe_pre_fetch(
        _args(state_root=str(state_root), library_root=str(tmp_path / "library")),
        profile,
        path,
        tmp_path,
        NOW,
    )

    cmd = captured["cmd"]
    assert cmd[:4] == ["uv", "run", "--project", "services/music-stack-cli"]
    assert "music-stack" in cmd
    assert "sync" in cmd
    assert "--profile" in cmd and str(path) in cmd
    assert "--global-config" in cmd and str(tmp_path / "global.yaml") in cmd
    assert "--source" in cmd and "apple_music" in cmd
    assert "--source" in cmd and "podcasts" in cmd
    assert "--playlist" in cmd and "Chill" in cmd

    with StateDB(state_root / "john.sqlite") as db:
        assert db.get_last_fetched("playlist", "Chill") == NOW
        assert db.get_last_fetched("podcast_show", "__all__") == NOW


def test_maybe_pre_fetch_failed_subprocess_does_not_record_fetch(monkeypatch, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", fetch_schedule="0 3 * * *")
    from common.config import load_profile_config

    profile = load_profile_config(path)

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, capture_output, text: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="boom"
        ),
    )

    state_root = tmp_path / "state"
    cli_module._maybe_pre_fetch(
        _args(state_root=str(state_root)), profile, path, tmp_path, NOW
    )

    with StateDB(state_root / "john.sqlite") as db:
        assert db.get_last_fetched("playlist", "Chill") is None
        assert db.get_last_fetched("podcast_show", "__all__") is None


# --- _cmd_auto_sync ------------------------------------------------------------


def test_cmd_auto_sync_matches_profile_and_runs_sync_with_removals_allowed(
    monkeypatch, tmp_path
):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    _write_profile(config_root / "profiles", "john.yaml", "john")
    (config_root / "global.yaml").write_text("paths: {}\nsources: {}\npodcasts: {}\n")

    from common.config import load_profile_config

    profile = load_profile_config(config_root / "profiles" / "john.yaml")

    monkeypatch.setattr(cli_module, "find_matching_profile", lambda profiles: profile)
    monkeypatch.setattr(cli_module, "mount_candidate_devices", lambda: [])

    captured = {}

    def fake_run_sync(args, matched_profile):
        captured["args"] = args
        captured["profile"] = matched_profile
        return 0

    monkeypatch.setattr(cli_module, "_run_sync", fake_run_sync)

    args = argparse.Namespace(
        config_root=str(config_root),
        library_root=str(tmp_path / "library"),
        state_root=str(tmp_path / "state"),
        wait_seconds=1.0,
        poll_interval=0.1,
        pre_fetch_horizon_hours=4.0,
        music_stack_project_dir="services/music-stack-cli",
        lock_timeout=5,
    )

    result = cli_module._cmd_auto_sync(args)

    assert result == 0
    assert captured["profile"] is profile
    assert captured["args"].execute is True
    assert captured["args"].allow_removals is True


def test_cmd_auto_sync_auto_mounts_before_matching_and_reports_it(
    monkeypatch, tmp_path, capsys
):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    _write_profile(config_root / "profiles", "john.yaml", "john")

    from common.config import load_profile_config

    profile = load_profile_config(config_root / "profiles" / "john.yaml")

    monkeypatch.setattr(cli_module, "mount_candidate_devices", lambda: ["/dev/sdb1"])
    monkeypatch.setattr(cli_module, "find_matching_profile", lambda profiles: profile)
    monkeypatch.setattr(cli_module, "_run_sync", lambda args, matched_profile: 0)

    args = argparse.Namespace(
        config_root=str(config_root),
        library_root=str(tmp_path / "library"),
        state_root=str(tmp_path / "state"),
        wait_seconds=1.0,
        poll_interval=0.1,
        pre_fetch_horizon_hours=4.0,
        music_stack_project_dir="services/music-stack-cli",
        lock_timeout=5,
    )

    result = cli_module._cmd_auto_sync(args)

    assert result == 0
    assert "auto-mounted /dev/sdb1" in capsys.readouterr().out


def test_cmd_auto_sync_fails_immediately_on_ambiguous_match(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    _write_profile(config_root / "profiles", "john.yaml", "john")

    def _raise(profiles):
        raise AmbiguousDeviceMatchError("connected device matches multiple profiles: a, b")

    monkeypatch.setattr(cli_module, "find_matching_profile", _raise)
    monkeypatch.setattr(cli_module, "mount_candidate_devices", lambda: [])
    run_sync_calls = []
    monkeypatch.setattr(cli_module, "_run_sync", lambda *a: run_sync_calls.append(a))

    args = argparse.Namespace(
        config_root=str(config_root),
        library_root=str(tmp_path / "library"),
        state_root=str(tmp_path / "state"),
        wait_seconds=1.0,
        poll_interval=0.1,
        pre_fetch_horizon_hours=4.0,
        music_stack_project_dir="services/music-stack-cli",
        lock_timeout=5,
    )

    result = cli_module._cmd_auto_sync(args)

    assert result == 1
    assert run_sync_calls == []


def _fake_plan(**overrides) -> SimpleNamespace:
    defaults = dict(
        to_add=[],
        to_remove=[],
        to_update_metadata=[],
        to_update_file=[],
        to_update_artwork=[],
        duplicates={},
        playlists_to_add=[],
        playlists_to_edit=[],
        playlists_to_remove=[],
        storage=SimpleNamespace(format=lambda: "0 B"),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_print_plan_playlist_add_prints_title_not_raw_dict(capsys):
    # Real iopenpod playlist dicts key the name as 'Title' (capitalized)
    # and carry a huge 'items' list (every track in the playlist) —
    # a real incident: p.get('title') (lowercase) missed that key
    # entirely and fell through to printing the whole dict, flooding
    # auto-sync.log with a wall of every track's source_path/db_track_id
    # per playlist and making an otherwise-clean sync look broken. See
    # notes.md.
    playlist = {
        "Title": "ALT CTRL",
        "playlist_id": 123,
        "items": [{"source_path": f"/library/music/track{i}.m4a"} for i in range(80)],
    }
    plan = _fake_plan(playlists_to_add=[playlist])

    cli_module._print_plan(plan)

    out = capsys.readouterr().out
    assert "+ playlist: ALT CTRL" in out
    assert "source_path" not in out
    assert "playlist_id" not in out


def test_print_plan_playlist_edit_prints_title_not_raw_dict(capsys):
    playlist = {"Title": "Chill", "playlist_id": 456, "items": []}
    plan = _fake_plan(playlists_to_edit=[playlist])

    cli_module._print_plan(plan)

    out = capsys.readouterr().out
    assert "~ playlist: Chill" in out
    assert "playlist_id" not in out


def test_cmd_auto_sync_fails_after_wait_seconds_exhausted_with_no_match(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    _write_profile(config_root / "profiles", "john.yaml", "john")

    def _raise(profiles):
        raise DeviceNotFoundError("no connected, mounted iPod matches volume_label='TEST'")

    monkeypatch.setattr(cli_module, "find_matching_profile", _raise)
    monkeypatch.setattr(cli_module, "mount_candidate_devices", lambda: [])
    run_sync_calls = []
    monkeypatch.setattr(cli_module, "_run_sync", lambda *a: run_sync_calls.append(a))

    args = argparse.Namespace(
        config_root=str(config_root),
        library_root=str(tmp_path / "library"),
        state_root=str(tmp_path / "state"),
        wait_seconds=0.2,
        poll_interval=0.05,
        pre_fetch_horizon_hours=4.0,
        music_stack_project_dir="services/music-stack-cli",
        lock_timeout=5,
    )

    result = cli_module._cmd_auto_sync(args)

    assert result == 1
    assert run_sync_calls == []
