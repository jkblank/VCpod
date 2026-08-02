from pathlib import Path

import pytest

from common.config import (
    ConfigError,
    load_all_profiles,
    load_global_config,
    load_profile_config,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_global_config_loads():
    config = load_global_config(REPO_ROOT / "config" / "global.yaml")
    assert config.sources.apple_music.enabled is True
    assert config.sources.ytmusic.enabled is True
    assert config.podcasts.pocketcasts.poll_interval_minutes == 60
    assert config.library_manager.dedup_enabled is True
    assert config.backups.default_keep_last == 3


def test_example_profiles_load():
    profiles = load_all_profiles(REPO_ROOT / "config" / "profiles")
    assert set(profiles) == {"alice", "bob", "john"}
    assert profiles["alice"].device.match_by == "serial"
    assert profiles["bob"].device.match_by == "volume_label"
    assert profiles["john"].device.match_by == "serial"
    assert profiles["john"].device.match_value == "8K6382K4V9S"
    assert profiles["bob"].podcasts.shows == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]
    assert profiles["alice"].fetch.schedule == "0 3 * * *"
    assert profiles["alice"].playlists[1].fetch_schedule == "0 */6 * * *"
    assert profiles["alice"].playlists[0].fetch_schedule is None
    assert profiles["bob"].fetch.schedule is None
    assert profiles["alice"].audiobooks is None
    assert profiles["bob"].audiobooks is None
    assert profiles["john"].audiobooks.mode == "include"
    assert profiles["john"].audiobooks.selections == []


def test_missing_file_raises():
    with pytest.raises(ConfigError, match="file not found"):
        load_global_config(REPO_ROOT / "config" / "does_not_exist.yaml")


def test_invalid_yaml_syntax_raises():
    with pytest.raises(ConfigError, match="invalid YAML"):
        load_profile_config(FIXTURES / "invalid_yaml.yaml")


def test_missing_required_field_raises():
    with pytest.raises(ConfigError, match="match_by"):
        load_profile_config(FIXTURES / "profile_missing_field.yaml")


def test_invalid_enum_value_raises():
    with pytest.raises(ConfigError, match="match_by"):
        load_profile_config(FIXTURES / "profile_bad_enum.yaml")


def test_wrong_field_type_raises():
    with pytest.raises(ConfigError, match="max_episodes_per_show"):
        load_profile_config(FIXTURES / "profile_wrong_type.yaml")


def test_invalid_cron_expression_raises():
    with pytest.raises(ConfigError, match="invalid cron expression"):
        load_profile_config(FIXTURES / "profile_bad_cron.yaml")


def test_invalid_shows_entry_raises():
    with pytest.raises(ConfigError, match="invalid shows entry"):
        load_profile_config(FIXTURES / "profile_bad_shows_entry.yaml")


def test_duplicate_profile_name_raises():
    with pytest.raises(ConfigError, match="duplicate profile name"):
        load_all_profiles(FIXTURES / "duplicate")


def test_reserved_profile_name_global_raises():
    # state_root/global.sqlite is reserved for fetch-scheduler's
    # cross-profile maintenance tasks — a profile named "global" would
    # silently collide with it via resolve_roots.
    with pytest.raises(ConfigError, match="reserved"):
        load_profile_config(FIXTURES / "profile_reserved_name.yaml")
