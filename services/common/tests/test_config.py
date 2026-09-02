from pathlib import Path

import pytest

from common.config import (
    ConfigError,
    load_all_profiles,
    load_global_config,
    load_profile_config,
    resolve_config_path,
    resolve_profile_path,
    save_global_config,
    save_profile_config,
)
from common.models import (
    DeviceMatch,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SyncSettings,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _minimal_profile(name: str) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="serial", match_value="ABC123"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file=f"/config/secrets/pocketcasts/{name}.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )


def test_global_config_loads():
    config = load_global_config(REPO_ROOT / "config" / "global.yaml")
    assert config.sources.apple_music.enabled is True
    assert config.sources.ytmusic.enabled is True
    assert config.podcasts.pocketcasts.poll_interval_minutes == 60
    assert config.library_manager.dedup_enabled is True
    assert config.backups.default_keep_last == 3


def test_example_profiles_load():
    profiles = load_all_profiles(REPO_ROOT / "config" / "profiles")
    # Subset, not equality: config/profiles/ can (and on a real dev
    # machine, does) hold real gitignored profiles beyond these four
    # tracked examples -- this only needs to confirm the tracked ones
    # still load correctly, not enumerate every profile on disk.
    assert {"alice", "bob", "john", "john-copy"} <= set(profiles)
    assert profiles["alice"].device.match_by == "serial"
    assert profiles["bob"].device.match_by == "volume_label"
    assert profiles["john"].device.match_by == "serial"
    assert profiles["john"].device.match_value == "8K13762U9ZS"
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


def test_resolve_profile_path_by_bare_name():
    resolved = resolve_profile_path("john", REPO_ROOT / "config")
    assert resolved == REPO_ROOT / "config" / "profiles" / "john.yaml"


def test_resolve_profile_path_literal_path_passthrough():
    literal = REPO_ROOT / "config" / "profiles" / "bob.yaml"
    assert resolve_profile_path(str(literal), REPO_ROOT / "config") == literal
    # A relative literal path that happens to resolve from CWD also wins
    # over name resolution — same "always accept a literal path" behavior
    # every existing --profile flag already has.
    assert resolve_profile_path(literal, REPO_ROOT / "config") == literal


def test_resolve_profile_path_unknown_name_lists_available():
    with pytest.raises(ConfigError) as exc_info:
        resolve_profile_path("nonexistent-profile", REPO_ROOT / "config")
    message = str(exc_info.value)
    assert "nonexistent-profile" in message
    assert "john" in message
    assert "alice" in message


def test_save_profile_config_round_trips(tmp_path):
    original = load_profile_config(REPO_ROOT / "config" / "profiles" / "alice.yaml")
    path = tmp_path / "alice.yaml"

    save_profile_config(original, path)
    reloaded = load_profile_config(path)

    assert reloaded == original


def test_save_profile_config_omits_unset_optional_sections(tmp_path):
    # external_library/audiobooks/music left unset (None) must not appear
    # in the written YAML at all -- their presence, even as `null`, means
    # something different from absence (see MusicLibraryConfig's own
    # opt-in-to-curation semantics).
    path = tmp_path / "new.yaml"

    save_profile_config(_minimal_profile("new"), path)

    written = path.read_text()
    assert "external_library" not in written
    assert "audiobooks" not in written
    assert "music" not in written


def test_save_profile_config_rejects_reserved_name(tmp_path):
    with pytest.raises(ConfigError, match="reserved"):
        save_profile_config(_minimal_profile("global"), tmp_path / "global.yaml")


def test_save_profile_config_rejects_duplicate_name(tmp_path):
    save_profile_config(_minimal_profile("dupe"), tmp_path / "first.yaml")

    with pytest.raises(ConfigError, match="duplicate profile name"):
        save_profile_config(_minimal_profile("dupe"), tmp_path / "second.yaml")


def test_save_profile_config_overwriting_same_path_is_allowed(tmp_path):
    path = tmp_path / "existing.yaml"
    save_profile_config(_minimal_profile("existing"), path)

    # Re-saving the same profile (e.g. an edit) to its own existing path
    # must not trip the duplicate-name check against itself.
    save_profile_config(_minimal_profile("existing"), path)

    assert load_profile_config(path).profile == "existing"


def test_save_global_config_round_trips(tmp_path):
    original = load_global_config(REPO_ROOT / "config" / "global.yaml")
    path = tmp_path / "global.yaml"

    save_global_config(original, path)
    reloaded = load_global_config(path)

    assert reloaded == original


def test_resolve_config_path_rewrites_config_container_prefix(tmp_path):
    resolved = resolve_config_path("/config/secrets/apple_music_cookies.txt", tmp_path)
    assert resolved == tmp_path / "secrets" / "apple_music_cookies.txt"


def test_resolve_config_path_falls_back_to_literal_path_when_not_config_rooted(tmp_path):
    resolved = resolve_config_path("/somewhere/else/creds.json", tmp_path)
    assert resolved == Path("/somewhere/else/creds.json")
