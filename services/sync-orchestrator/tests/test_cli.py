from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from common.config import ConfigError
from common.state import EpisodeRecord, StateDB
from sync_orchestrator import cli as cli_module
from sync_orchestrator.device import AmbiguousDeviceMatchError, DeviceNotFoundError

NOW = datetime(2026, 7, 25, 22, 0, tzinfo=timezone.utc)


def test_default_music_stack_project_dir_is_absolute_and_cwd_independent():
    # Confirmed live (2026-08-18): a relative "services/music-stack-cli"
    # default only worked when invoked from the repo root -- every manual
    # `sync-orchestrator sync` run that session was from inside
    # services/sync-orchestrator/ instead, where that relative path
    # resolved to nothing and the play-status push subprocess silently
    # failed with "No such file or directory", while the device sync
    # itself still reported PASS.
    result = cli_module._default_music_stack_project_dir()

    path = Path(result)
    assert path.is_absolute()
    assert path.name == "music-stack-cli"
    assert (path / "pyproject.toml").is_file()


def _write_profile(
    directory: Path,
    filename: str,
    profile_name: str,
    *,
    fetch_schedule: str | None = None,
    push_play_status_back: bool = False,
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
  push_play_status_back: {"true" if push_play_status_back else "false"}
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


# --- _maybe_push_play_status ---------------------------------------------------


def _record_pending_episode(state_db_path: Path) -> None:
    with StateDB(state_db_path) as db:
        db.record_episode(
            EpisodeRecord(
                episode_uuid="ep-1",
                podcast_uuid="show-1",
                show_name="Test Show",
                local_path="/does/not/matter.mp3",
                played=False,
                played_up_to=0,
                downloaded_at="2026-07-19T00:00:00+00:00",
            )
        )
        db.update_play_state("ep-1", played=True, played_up_to=900)


def test_maybe_push_play_status_skips_when_nothing_pending(monkeypatch, tmp_path):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", push_play_status_back=True)
    from common.config import load_profile_config

    profile = load_profile_config(path)
    state_root = tmp_path / "state"
    state_root.mkdir()
    with StateDB(state_root / "john.sqlite"):
        pass  # a real db with nothing pending

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    cli_module._maybe_push_play_status(
        _args(state_root=str(state_root)), profile, path, tmp_path
    )

    assert calls == []


def test_maybe_push_play_status_skips_when_state_db_does_not_exist_yet(monkeypatch, tmp_path):
    # A device sync's first-ever run for a profile has no state db yet --
    # must not crash trying to check it for pending pushes.
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", push_play_status_back=True)
    from common.config import load_profile_config

    profile = load_profile_config(path)

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    cli_module._maybe_push_play_status(
        _args(state_root=str(tmp_path / "does-not-exist")), profile, path, tmp_path
    )

    assert calls == []


def test_maybe_push_play_status_skips_when_disabled_in_profile(monkeypatch, tmp_path):
    # push_play_status_back existed in the schema but was never read by
    # anything until this feature -- confirm it's a real gate now.
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", push_play_status_back=False)
    from common.config import load_profile_config

    profile = load_profile_config(path)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _record_pending_episode(state_root / "john.sqlite")

    calls = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append((a, k)))

    cli_module._maybe_push_play_status(
        _args(state_root=str(state_root)), profile, path, tmp_path
    )

    assert calls == []


def test_maybe_push_play_status_invokes_subprocess_when_something_pending(monkeypatch, tmp_path):
    # Confirmed live (2026-08-18): gating on just this run's own
    # play_states_updated missed anything already pending from before
    # this run (a manual override, or an earlier push lost to a race) --
    # this must check the state db's real pending_push count instead.
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", push_play_status_back=True)
    from common.config import load_profile_config

    profile = load_profile_config(path)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _record_pending_episode(state_root / "john.sqlite")

    captured = {}

    def fake_run(cmd, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="Pushed play state for 1 episode(s)\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    cli_module._maybe_push_play_status(
        _args(library_root=str(tmp_path / "library"), state_root=str(state_root)),
        profile,
        path,
        tmp_path,
    )

    cmd = captured["cmd"]
    assert cmd[:4] == ["uv", "run", "--project", "services/music-stack-cli"]
    assert "music-stack" in cmd
    assert "sync" in cmd
    assert "--profile" in cmd and str(path) in cmd
    assert "--global-config" in cmd and str(tmp_path / "global.yaml") in cmd
    assert "--source" in cmd and "podcasts" in cmd
    # Must not pull in music/playlist sources too -- this is purely the
    # play-state round trip, not a general fetch.
    assert "apple_music" not in cmd
    assert "ytmusic" not in cmd


def test_maybe_push_play_status_failed_subprocess_does_not_raise(monkeypatch, tmp_path, capsys):
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    path = _write_profile(profiles_dir, "john.yaml", "john", push_play_status_back=True)
    from common.config import load_profile_config

    profile = load_profile_config(path)
    state_root = tmp_path / "state"
    state_root.mkdir()
    _record_pending_episode(state_root / "john.sqlite")

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, capture_output, text: subprocess.CompletedProcess(
            cmd, 1, stdout="", stderr="ERROR: could not authenticate"
        ),
    )

    # Must not raise -- a failed push is a warning, not a sync failure.
    cli_module._maybe_push_play_status(
        _args(state_root=str(state_root)), profile, path, tmp_path
    )

    assert "WARNING: play-status push failed" in capsys.readouterr().out


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

    def fake_run_sync(args, matched_profile, **kwargs):
        captured["args"] = args
        captured["profile"] = matched_profile
        captured["kwargs"] = kwargs
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
    monkeypatch.setattr(cli_module, "_run_sync", lambda args, matched_profile, **kwargs: 0)

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
    monkeypatch.setattr(cli_module, "_run_sync", lambda *a, **k: run_sync_calls.append(a))

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


def test_print_plan_playlist_remove_prints_title_not_raw_dict(capsys):
    # Regression: playlists_to_remove had no per-item printing at all before
    # this test existed -- a real removal (see cli.py's playlists_to_remove
    # gate) was invisible in the plan output, findable only by count. See
    # notes.md.
    playlist = {"Title": "Playlist", "playlist_id": 789, "items": []}
    plan = _fake_plan(playlists_to_remove=[playlist])

    cli_module._print_plan(plan)

    out = capsys.readouterr().out
    assert "- playlist: Playlist" in out
    assert "playlist_id" not in out


# --- _run_sync's removal safety gate -------------------------------------


def _fake_planned(**overrides) -> SimpleNamespace:
    defaults = dict(
        plan=_fake_plan(),
        device_info=SimpleNamespace(path="/mnt/ipod"),
        before_track_count=0,
        snapshot=None,
        unresolved_selections=[],
        unresolved_audiobook_selections=[],
        play_states_updated=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _run_sync_args(**overrides) -> argparse.Namespace:
    base = dict(
        library_root="/library",
        state_root="/state",
        pc_folders=None,
        skip_backup=True,
        skip_podcasts=False,
        execute=True,
        allow_removals=False,
        skip_eject=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def test_run_sync_refuses_execute_when_playlist_removal_proposed_without_allow_removals(
    monkeypatch, tmp_path
):
    # Regression: plan.playlists_to_remove is a separate list from
    # plan.to_remove (tracks) on SyncPlan -- the removal safety gate only
    # checked the latter, so a plain --execute (no --allow-removals) would
    # have removed a real on-device playlist with zero review step. See
    # notes.md.
    profile = SimpleNamespace(
        profile="john",
        device=SimpleNamespace(match_by="volume_label", match_value="TEST"),
    )
    monkeypatch.setattr(
        cli_module, "find_matching_device",
        lambda match: SimpleNamespace(
            path="/mnt/ipod", model_family="iPod", generation="5.5th Gen",
            model_number="MA450", capacity="80GB",
        ),
    )
    playlist = {"Title": "Playlist", "playlist_id": 1}
    planned = _fake_planned(plan=_fake_plan(playlists_to_remove=[playlist]))
    monkeypatch.setattr(cli_module, "plan_sync", lambda **kwargs: planned)
    execute_calls = []
    monkeypatch.setattr(
        cli_module, "execute_sync", lambda *a, **k: execute_calls.append(a) or (None, None)
    )

    result = cli_module._run_sync(
        _run_sync_args(allow_removals=False),
        profile,
        profile_path=Path("/config/profiles/john.yaml"),
        config_root=Path("/config"),
    )

    assert result == 1
    assert execute_calls == []


def test_run_sync_allows_execute_with_playlist_removal_when_allow_removals_set(
    monkeypatch, tmp_path
):
    profile = SimpleNamespace(
        profile="john",
        device=SimpleNamespace(match_by="volume_label", match_value="TEST"),
        sync=SimpleNamespace(push_play_status_back=False),
    )
    monkeypatch.setattr(
        cli_module, "find_matching_device",
        lambda match: SimpleNamespace(
            path="/mnt/ipod", model_family="iPod", generation="5.5th Gen",
            model_number="MA450", capacity="80GB",
        ),
    )
    playlist = {"Title": "Playlist", "playlist_id": 1}
    planned = _fake_planned(plan=_fake_plan(playlists_to_remove=[playlist]))
    monkeypatch.setattr(cli_module, "plan_sync", lambda **kwargs: planned)
    execute_calls = []
    monkeypatch.setattr(
        cli_module, "execute_sync",
        lambda *a, **k: execute_calls.append(a) or (
            SimpleNamespace(summary="done", tracks_added=0), {"mhlt": []}
        ),
    )

    result = cli_module._run_sync(
        _run_sync_args(allow_removals=True),
        profile,
        profile_path=Path("/config/profiles/john.yaml"),
        config_root=Path("/config"),
    )

    assert result == 0
    assert len(execute_calls) == 1


def test_cmd_auto_sync_fails_after_wait_seconds_exhausted_with_no_match(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    _write_profile(config_root / "profiles", "john.yaml", "john")

    def _raise(profiles):
        raise DeviceNotFoundError("no connected, mounted iPod matches volume_label='TEST'")

    monkeypatch.setattr(cli_module, "find_matching_profile", _raise)
    monkeypatch.setattr(cli_module, "mount_candidate_devices", lambda: [])
    run_sync_calls = []
    monkeypatch.setattr(cli_module, "_run_sync", lambda *a, **k: run_sync_calls.append(a))

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
