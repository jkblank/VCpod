import pytest
from pydantic import ValidationError

from common.models import (
    AudiobooksConfig,
    BackupMaintenanceConfig,
    ExternalLibraryConfig,
    FetchSettings,
    LibraryManagerConfig,
    MusicLibraryConfig,
    PlaylistEntry,
    ProfileBackupRetention,
    ProfilePodcastsConfig,
    ShowOverride,
    SyncSettings,
)


def test_external_library_flat_string_selections_unchanged():
    cfg = ExternalLibraryConfig(path="/library", selections=["Linkin Park", "The Cure"])
    assert cfg.selections == ["Linkin Park", "The Cure"]


def test_external_library_nested_mapping_selection_flattened():
    cfg = ExternalLibraryConfig(
        path="/library",
        selections=[
            "Alanis Morissette",
            {"Talking Heads": ["Performance", "Remixed", "The Collection"]},
        ],
    )
    assert cfg.selections == [
        "Alanis Morissette",
        "Talking Heads/Performance",
        "Talking Heads/Remixed",
        "Talking Heads/The Collection",
    ]


def test_external_library_nested_mapping_multiple_artists_in_one_entry():
    cfg = ExternalLibraryConfig(
        path="/library",
        selections=[{"A": ["X"], "B": ["Y", "Z"]}],
    )
    assert cfg.selections == ["A/X", "B/Y", "B/Z"]


def test_external_library_invalid_nested_selection_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        ExternalLibraryConfig(path="/library", selections=[{"Talking Heads": "Performance"}])


def test_external_library_invalid_selection_type_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        ExternalLibraryConfig(path="/library", selections=[123])


def test_audiobooks_config_defaults_to_include_all():
    cfg = AudiobooksConfig()
    assert cfg.mode == "include"
    assert cfg.selections == []


def test_audiobooks_flat_string_selections_unchanged():
    cfg = AudiobooksConfig(selections=["Franz Kafka", "Franz Kafka/The Trial"])
    assert cfg.selections == ["Franz Kafka", "Franz Kafka/The Trial"]


def test_audiobooks_nested_mapping_selection_flattened():
    cfg = AudiobooksConfig(
        mode="exclude",
        selections=[
            "Franz Kafka",
            {"George Orwell": ["1984", "Animal Farm"]},
        ],
    )
    assert cfg.mode == "exclude"
    assert cfg.selections == [
        "Franz Kafka",
        "George Orwell/1984",
        "George Orwell/Animal Farm",
    ]


def test_audiobooks_invalid_nested_selection_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        AudiobooksConfig(selections=[{"George Orwell": "1984"}])


def test_audiobooks_invalid_selection_type_raises():
    with pytest.raises(ValidationError, match="invalid selections entry"):
        AudiobooksConfig(selections=[123])


def test_audiobooks_invalid_mode_raises():
    with pytest.raises(ValidationError):
        AudiobooksConfig(mode="whitelist")


def _podcasts_config(**overrides):
    base = dict(
        pocketcasts={"credentials_file": "/config/secrets/pocketcasts/x.json"},
        sync_unplayed_only=True,
        max_episodes_per_show=5,
    )
    base.update(overrides)
    return ProfilePodcastsConfig(**base)


def test_fetch_settings_default_schedule_is_none():
    assert FetchSettings().schedule is None


def test_fetch_settings_accepts_valid_cron_expression():
    assert FetchSettings(schedule="0 3 * * *").schedule == "0 3 * * *"


def test_fetch_settings_rejects_invalid_cron_expression():
    with pytest.raises(ValidationError, match="invalid cron expression"):
        FetchSettings(schedule="not a cron")


def test_playlist_entry_fetch_schedule_defaults_to_none():
    entry = PlaylistEntry(name="Chill", source="apple_music", source_id="pl.1")
    assert entry.fetch_schedule is None


def test_playlist_entry_rejects_invalid_fetch_schedule():
    with pytest.raises(ValidationError, match="invalid cron expression"):
        PlaylistEntry(name="Chill", source="apple_music", source_id="pl.1", fetch_schedule="nope")


def test_podcasts_shows_plain_string_list_unchanged():
    cfg = _podcasts_config(shows=["Daily News", "Weekly Deep Dive"])
    assert cfg.shows == ["Daily News", "Weekly Deep Dive"]
    assert cfg.show_names == ["Daily News", "Weekly Deep Dive"]


def test_podcasts_shows_all_shorthand_unchanged():
    cfg = _podcasts_config(shows="all")
    assert cfg.shows == "all"
    assert cfg.show_names == "all"


def test_podcasts_shows_mixed_string_and_override_entry():
    cfg = _podcasts_config(
        shows=["Daily News", {"Weekly Deep Dive": {"fetch_schedule": "0 6 * * 1"}}]
    )
    assert cfg.shows[0] == "Daily News"
    assert isinstance(cfg.shows[1], ShowOverride)
    assert cfg.shows[1].name == "Weekly Deep Dive"
    assert cfg.shows[1].fetch_schedule == "0 6 * * 1"
    assert cfg.show_names == ["Daily News", "Weekly Deep Dive"]


def test_podcasts_shows_accepts_already_constructed_show_override_instance():
    # Not just YAML-shaped dicts — code that builds ProfilePodcastsConfig
    # directly (tests, other services) may pass an already-constructed
    # ShowOverride in the list.
    cfg = _podcasts_config(shows=["Daily News", ShowOverride(name="Weekly Deep Dive")])
    assert cfg.shows[1] == ShowOverride(name="Weekly Deep Dive", fetch_schedule=None)
    assert cfg.show_names == ["Daily News", "Weekly Deep Dive"]


def test_podcasts_shows_override_entry_without_overrides():
    cfg = _podcasts_config(shows=[{"Weekly Deep Dive": {}}])
    assert cfg.shows[0] == ShowOverride(name="Weekly Deep Dive", fetch_schedule=None)


def test_podcasts_shows_override_entry_with_null_overrides():
    cfg = _podcasts_config(shows=[{"Weekly Deep Dive": None}])
    assert cfg.shows[0] == ShowOverride(name="Weekly Deep Dive", fetch_schedule=None)


def test_podcasts_shows_invalid_entry_multi_key_dict_raises():
    with pytest.raises(ValidationError, match="invalid shows entry"):
        _podcasts_config(shows=[{"A": {}, "B": {}}])


def test_podcasts_shows_invalid_entry_non_dict_overrides_raises():
    with pytest.raises(ValidationError, match="expected a mapping of overrides"):
        _podcasts_config(shows=[{"Weekly Deep Dive": "not a mapping"}])


def test_podcasts_shows_invalid_entry_type_raises():
    with pytest.raises(ValidationError, match="invalid shows entry"):
        _podcasts_config(shows=[123])


def test_podcasts_fetch_schedule_defaults_to_none():
    assert _podcasts_config().fetch_schedule is None


def test_podcasts_fetch_schedule_rejects_invalid_cron():
    with pytest.raises(ValidationError, match="invalid cron expression"):
        _podcasts_config(fetch_schedule="nope")


def test_library_manager_config_defaults():
    cfg = LibraryManagerConfig()
    assert cfg.dedup_enabled is False
    assert cfg.cleanup_enabled is False
    assert cfg.normalize_artwork_enabled is False
    assert cfg.fuzzy_threshold == 92.0
    assert cfg.quarantine_older_than_days == 14


def test_library_manager_config_rejects_out_of_range_fuzzy_threshold():
    with pytest.raises(ValidationError):
        LibraryManagerConfig(fuzzy_threshold=101)
    with pytest.raises(ValidationError):
        LibraryManagerConfig(fuzzy_threshold=0)


def test_backup_maintenance_config_defaults():
    cfg = BackupMaintenanceConfig()
    assert cfg.prune_enabled is False
    assert cfg.default_keep_last == 3
    assert cfg.default_max_age_days == 14


def test_backup_maintenance_config_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        BackupMaintenanceConfig(default_keep_last=0)
    with pytest.raises(ValidationError):
        BackupMaintenanceConfig(default_max_age_days=0)


def test_profile_backup_retention_defaults_to_none():
    cfg = ProfileBackupRetention()
    assert cfg.keep_last is None
    assert cfg.max_age_days is None


def test_profile_backup_retention_rejects_non_positive_values():
    with pytest.raises(ValidationError):
        ProfileBackupRetention(keep_last=0)
    with pytest.raises(ValidationError):
        ProfileBackupRetention(max_age_days=-1)


def _sync_settings(**overrides):
    base = dict(trigger="on_connect", transcode_format="aac", push_play_status_back=False)
    base.update(overrides)
    return SyncSettings(**base)


def test_sync_settings_mode_defaults_to_itunes():
    assert _sync_settings().mode == "itunes"


def test_sync_settings_mode_accepts_rockbox():
    assert _sync_settings(mode="rockbox").mode == "rockbox"


def test_sync_settings_mode_rejects_unknown_value():
    with pytest.raises(ValidationError):
        _sync_settings(mode="winamp")


def test_music_library_config_defaults_to_include_nothing():
    # Deliberately the opposite default from AudiobooksConfig's "empty +
    # include = sync everything" — a profile that sets `music:` at all is
    # opting into curation, so an empty whitelist curates down to nothing
    # extra (playlist tracks are still always included regardless, at
    # the sync-orchestrator layer, not this schema).
    cfg = MusicLibraryConfig()
    assert cfg.mode == "include"
    assert cfg.selections == []


def test_music_library_config_nested_mapping_selection_flattened():
    cfg = MusicLibraryConfig(selections=["Radiohead", {"Talking Heads": ["Remixed"]}])
    assert cfg.selections == ["Radiohead", "Talking Heads/Remixed"]


def test_music_library_config_invalid_mode_raises():
    with pytest.raises(ValidationError):
        MusicLibraryConfig(mode="whitelist")


def test_profile_config_music_defaults_to_none():
    from common.models import DeviceMatch, ProfileConfig, ProfilePocketCastsConfig

    profile = ProfileConfig(
        profile="test",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=_sync_settings(),
    )
    assert profile.music is None
