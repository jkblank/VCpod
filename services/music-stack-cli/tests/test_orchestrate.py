from pathlib import Path

from common.models import (
    AppleMusicSource,
    DeviceMatch,
    GlobalConfig,
    Paths,
    PlaylistEntry,
    PocketCastsGlobalConfig,
    PodcastsGlobalConfig,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    ShowOverride,
    SourcesConfig,
    SpotifySource,
    SyncSettings,
    YtMusicSource,
)

from music_stack_cli import orchestrate as orchestrate_module
from music_stack_cli.orchestrate import (
    resolve_config_path,
    resolve_roots,
    run_sync,
    select_playlists,
)


def test_resolve_config_path_rewrites_config_container_prefix(tmp_path):
    resolved = resolve_config_path("/config/secrets/apple_music_cookies.txt", tmp_path)
    assert resolved == tmp_path / "secrets" / "apple_music_cookies.txt"


def test_resolve_config_path_falls_back_to_literal_path_when_not_config_rooted(tmp_path):
    resolved = resolve_config_path("/somewhere/else/creds.json", tmp_path)
    assert resolved == Path("/somewhere/else/creds.json")


def test_resolve_roots_splits_library_root_into_music_playlists_podcasts(tmp_path):
    library_root = tmp_path / "library"
    state_root = tmp_path / "state"

    roots = resolve_roots(library_root, state_root, "john")

    assert roots.music_library_root == library_root / "music"
    assert roots.playlists_root == library_root / "playlists"
    assert roots.podcasts_library_root == library_root / "podcasts"
    assert roots.state_db_path == state_root / "john.sqlite"


def _entry(name: str, source: str) -> PlaylistEntry:
    return PlaylistEntry(name=name, source=source, source_id=f"id-{name}")


def test_select_playlists_filters_by_source():
    playlists = [_entry("Chill", "apple_music"), _entry("Semaphore", "ytmusic")]

    matched, unmatched = select_playlists(playlists, {"apple_music"}, None)

    assert [p.name for p in matched] == ["Chill"]
    assert unmatched == []


def test_select_playlists_filters_by_name_across_selected_sources():
    playlists = [
        _entry("Chill", "apple_music"),
        _entry("Semaphore", "ytmusic"),
        _entry("Elevate", "apple_music"),
    ]

    matched, unmatched = select_playlists(
        playlists, {"apple_music", "ytmusic"}, ["Chill", "Semaphore"]
    )

    assert {p.name for p in matched} == {"Chill", "Semaphore"}
    assert unmatched == []


def test_select_playlists_reports_unmatched_names():
    playlists = [_entry("Chill", "apple_music")]

    matched, unmatched = select_playlists(playlists, {"apple_music"}, ["Chill", "Nonexistent"])

    assert [p.name for p in matched] == ["Chill"]
    assert unmatched == ["Nonexistent"]


def test_select_playlists_name_filter_respects_source_restriction():
    # A playlist named "Chill" exists, but only under ytmusic is selected —
    # the apple_music one shouldn't match even though the name is right.
    playlists = [_entry("Chill", "apple_music")]

    matched, unmatched = select_playlists(playlists, {"ytmusic"}, ["Chill"])

    assert matched == []
    assert unmatched == ["Chill"]


def _global_config(tmp_path: Path, oauth_file: str) -> GlobalConfig:
    return GlobalConfig(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(enabled=True, cookies_file="/config/secrets/apple.txt"),
            spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify.json"),
            ytmusic=YtMusicSource(
                enabled=True, oauth_file=oauth_file, cookies_file="/config/secrets/yt.txt"
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
    )


def _profile_config() -> ProfileConfig:
    return ProfileConfig(
        profile="john",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[_entry("Semaphore", "ytmusic")],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )


def test_run_sync_prunes_stale_playlist_no_longer_in_profile(monkeypatch, tmp_path):
    # Found live 2026-08-24: a playlist removed from a profile's own
    # playlists: list never got removed from the device, because nothing
    # in the fetch pipeline ever deleted its stale .m3u8 — sync-
    # orchestrator's device sync only ever sees whatever files physically
    # exist under playlists_root/{profile}. run_sync (the fetch stage)
    # must prune it, since it's the only thing that owns this folder.
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = _profile_config()  # only configures "Semaphore" (ytmusic)
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    (roots.playlists_root / "john").mkdir(parents=True)
    (roots.playlists_root / "john" / "Semaphore.m3u8").write_text("#EXTM3U\n")
    (roots.playlists_root / "john" / "Every1.m3u8").write_text("#EXTM3U\n")

    monkeypatch.setattr(orchestrate_module, "fetch_ytmusic_playlists", lambda entries, **kw: [])

    result = run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"ytmusic"},
        playlist_names=None,
        show_selectors=None,
    )

    assert result.pruned_playlists == ["Every1"]
    assert (roots.playlists_root / "john" / "Semaphore.m3u8").is_file()
    assert not (roots.playlists_root / "john" / "Every1.m3u8").exists()


def test_run_sync_playlist_narrowing_does_not_prune_the_rest(monkeypatch, tmp_path):
    # --playlist narrowing is a per-run scope choice, not an "I removed
    # this from my profile" signal -- must not prune a still-configured
    # playlist just because this particular run didn't fetch it.
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = _profile_config()
    profile = profile.model_copy(
        update={"playlists": [_entry("Semaphore", "ytmusic"), _entry("Chill", "ytmusic")]}
    )
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    (roots.playlists_root / "john").mkdir(parents=True)
    (roots.playlists_root / "john" / "Semaphore.m3u8").write_text("#EXTM3U\n")
    (roots.playlists_root / "john" / "Chill.m3u8").write_text("#EXTM3U\n")

    monkeypatch.setattr(orchestrate_module, "fetch_ytmusic_playlists", lambda entries, **kw: [])

    result = run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"ytmusic"},
        playlist_names=["Semaphore"],  # narrowed to just one playlist this run
        show_selectors=None,
    )

    assert result.pruned_playlists == []
    assert (roots.playlists_root / "john" / "Chill.m3u8").is_file()


def test_run_sync_ytmusic_omits_oauth_path_when_file_does_not_exist(monkeypatch, tmp_path):
    # Confirmed live: oauth is optional (get_playlist_tracks works fine
    # unauthenticated against public playlists), but resolve_config_path
    # always computes *a* path regardless of whether the file exists —
    # passing a nonexistent path straight through made ytmusicapi's
    # YTMusic(auth=...) try to parse it as an auth string and crash the
    # whole sync, instead of just skipping auth like the standalone
    # fetcher-ytmusic CLI already correctly does.
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = _profile_config()
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    captured = {}

    def fake_fetch_ytmusic_playlists(entries, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        orchestrate_module, "fetch_ytmusic_playlists", fake_fetch_ytmusic_playlists
    )

    run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"ytmusic"},
        playlist_names=None,
        show_selectors=None,
    )

    assert captured["oauth_path"] is None


def test_run_sync_ytmusic_passes_oauth_path_when_file_exists(monkeypatch, tmp_path):
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    oauth_path_on_disk = config_root / "secrets" / "ytmusic_oauth.json"
    oauth_path_on_disk.write_text("{}")

    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = _profile_config()
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    captured = {}

    def fake_fetch_ytmusic_playlists(entries, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        orchestrate_module, "fetch_ytmusic_playlists", fake_fetch_ytmusic_playlists
    )

    run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"ytmusic"},
        playlist_names=None,
        show_selectors=None,
    )

    assert captured["oauth_path"] == oauth_path_on_disk


def test_run_sync_podcasts_uses_show_names_not_raw_shows_entries(monkeypatch, tmp_path):
    # profile.podcasts.shows can now contain ShowOverride objects (see
    # common.models); resolve_show_selection only understands plain
    # strings, so run_sync must filter through show_names, not shows.
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    credentials_path = config_root / "secrets" / "pocketcasts.json"
    credentials_path.write_text('{"email": "a@example.com", "password": "x"}')

    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = ProfileConfig(
        profile="john",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file="/config/secrets/pocketcasts.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
            shows=["Daily News", ShowOverride(name="Weekly Deep Dive", fetch_schedule=None)],
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    captured = {}

    monkeypatch.setattr(
        orchestrate_module, "load_credentials", lambda path: ("a@example.com", "x")
    )
    monkeypatch.setattr(orchestrate_module, "login", lambda email, password: "token")
    monkeypatch.setattr(orchestrate_module, "list_subscriptions", lambda token: [])

    def fake_resolve_show_selection(subscriptions, wanted):
        captured["wanted"] = wanted
        return [], list(wanted)

    monkeypatch.setattr(
        orchestrate_module, "resolve_show_selection", fake_resolve_show_selection
    )

    run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"podcasts"},
        playlist_names=None,
        show_selectors=None,
    )

    assert captured["wanted"] == ["Daily News", "Weekly Deep Dive"]


def test_run_sync_podcasts_pushes_play_status_before_pruning(monkeypatch, tmp_path):
    # push_pending_play_status must run before prune_unsubscribed_shows so
    # a locally-confirmed play (device read-back or a manual override)
    # reaches Pocket Casts before this same run's subsequent state reads --
    # this used to only happen via a separate, never-automatically-invoked
    # CLI command (podcast-manager push-play-status). See
    # push_pending_play_status's own docstring.
    config_root = tmp_path / "config"
    (config_root / "secrets").mkdir(parents=True)
    credentials_path = config_root / "secrets" / "pocketcasts.json"
    credentials_path.write_text('{"email": "a@example.com", "password": "x"}')

    global_config = _global_config(tmp_path, oauth_file="/config/secrets/ytmusic_oauth.json")
    profile = ProfileConfig(
        profile="john",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file="/config/secrets/pocketcasts.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
            shows="all",
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=True),
    )
    roots = resolve_roots(tmp_path / "library", tmp_path / "state", "john")

    call_order = []

    monkeypatch.setattr(
        orchestrate_module, "load_credentials", lambda path: ("a@example.com", "x")
    )
    monkeypatch.setattr(orchestrate_module, "login", lambda email, password: "the-token")
    monkeypatch.setattr(orchestrate_module, "list_subscriptions", lambda token: [])

    fake_pushed = [object()]
    fake_failed = [(object(), "network blip")]

    def fake_push(token, *, state_db_path, lock_timeout):
        assert token == "the-token"
        call_order.append("push")
        return fake_pushed, fake_failed

    def fake_prune(subscriptions, *, state_db_path, lock_timeout):
        call_order.append("prune")
        return []

    monkeypatch.setattr(orchestrate_module, "push_pending_play_status", fake_push)
    monkeypatch.setattr(orchestrate_module, "prune_unsubscribed_shows", fake_prune)

    result = run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources={"podcasts"},
        playlist_names=None,
        show_selectors=None,
    )

    assert call_order == ["push", "prune"]
    assert result.pushed_play_status is fake_pushed
    assert result.failed_play_status_pushes is fake_failed
