import os
import socket
import time

import pytest
from fastapi.testclient import TestClient

from common.config import save_global_config, save_profile_config
from common.models import (
    AppleMusicSource,
    DeviceMatch,
    GlobalConfig,
    LibraryManagerConfig,
    Paths,
    PocketCastsGlobalConfig,
    PodcastsGlobalConfig,
    ProfileAppleMusicOverride,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    ProfileSourcesConfig,
    SourcesConfig,
    SpotifySource,
    SyncSettings,
    YtMusicSource,
)
from web_gui_backend.app import create_app

VALID_APPLE_COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".music.apple.com\tTRUE\t/\tTRUE\t2145916800\tmedia-user-token\tabc123token\n"
)


def _global_config(**overrides) -> GlobalConfig:
    base = dict(
        paths=Paths(library_root="/data/library", state_root="/data/state"),
        sources=SourcesConfig(
            apple_music=AppleMusicSource(
                enabled=True, cookies_file="/config/secrets/apple_music_cookies.txt"
            ),
            spotify=SpotifySource(
                enabled=True, credentials_file="/config/secrets/spotify_credentials.json"
            ),
            ytmusic=YtMusicSource(
                enabled=True,
                oauth_file="/config/secrets/ytmusic_oauth.json",
                cookies_file="/config/secrets/youtube_cookies.txt",
                oauth_client_file="/config/secrets/ytmusic_oauth_client.json",
                pot_provider_url="http://127.0.0.1:1",  # unreachable by default in tests
            ),
        ),
        podcasts=PodcastsGlobalConfig(pocketcasts=PocketCastsGlobalConfig(poll_interval_minutes=60)),
        library_manager=LibraryManagerConfig(),
    )
    base.update(overrides)
    return GlobalConfig(**base)


def _profile(name: str, **overrides) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="serial", match_value=f"SERIAL-{name}"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(
                credentials_file=f"/config/secrets/pocketcasts/{name}.json"
            ),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
        **overrides,
    )


@pytest.fixture
def config_root(tmp_path):
    (tmp_path / "profiles").mkdir()
    save_global_config(_global_config(), tmp_path / "global.yaml")
    return tmp_path


def _client(config_root) -> TestClient:
    return TestClient(create_app(config_root=config_root))


def _kinds(body: dict) -> list[str]:
    return [a["kind"] for a in body["alerts"]]


def test_alerts_flags_missing_global_credentials(config_root):
    resp = _client(config_root).get("/api/alerts")

    assert resp.status_code == 200
    body = resp.json()
    apple = next(a for a in body["alerts"] if a["kind"] == "Apple Music cookies")
    assert apple["severity"] == "missing"
    assert apple["profile"] is None
    yt = next(a for a in body["alerts"] if a["kind"] == "YouTube Music cookies")
    assert yt["severity"] == "missing"
    # Spotify is shelved -- never surfaced here even though it's "enabled"
    # in this fixture's global config, to avoid noise on an unused source.
    assert not any("Spotify" in kind for kind in _kinds(body))


def test_alerts_no_credential_alert_when_source_disabled(config_root):
    save_global_config(
        _global_config(
            sources=SourcesConfig(
                apple_music=AppleMusicSource(enabled=False, cookies_file="/config/secrets/apple.txt"),
                spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/spotify.json"),
                ytmusic=YtMusicSource(
                    enabled=False,
                    oauth_file="/config/secrets/oauth.json",
                    cookies_file="/config/secrets/yt.txt",
                ),
            )
        ),
        config_root / "global.yaml",
    )

    body = _client(config_root).get("/api/alerts").json()

    assert body["alerts"] == []


def test_alerts_fresh_credential_file_produces_no_alert(config_root):
    cookies_path = config_root / "secrets" / "apple_music_cookies.txt"
    cookies_path.parent.mkdir(parents=True)
    cookies_path.write_text(VALID_APPLE_COOKIES)

    body = _client(config_root).get("/api/alerts").json()

    assert not any(a["kind"] == "Apple Music cookies" for a in body["alerts"])


def test_alerts_stale_credential_file_flagged(config_root):
    cookies_path = config_root / "secrets" / "apple_music_cookies.txt"
    cookies_path.parent.mkdir(parents=True)
    cookies_path.write_text(VALID_APPLE_COOKIES)
    old = time.time() - (20 * 86400)  # 20 days old, past the 14-day threshold
    os.utime(cookies_path, (old, old))

    body = _client(config_root).get("/api/alerts").json()

    apple = next(a for a in body["alerts"] if a["kind"] == "Apple Music cookies")
    assert apple["severity"] == "stale"
    assert "not updated in" in apple["message"] and "d" in apple["message"]


def test_alerts_pot_provider_unreachable_flagged(config_root):
    body = _client(config_root).get("/api/alerts").json()

    pot = next(a for a in body["alerts"] if a["kind"] == "PO-token provider")
    assert pot["severity"] == "unreachable"


def test_alerts_pot_provider_reachable_no_alert(config_root):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        save_global_config(
            _global_config(
                sources=SourcesConfig(
                    apple_music=AppleMusicSource(enabled=False, cookies_file="/config/secrets/a.txt"),
                    spotify=SpotifySource(enabled=False, credentials_file="/config/secrets/s.json"),
                    ytmusic=YtMusicSource(
                        enabled=True,
                        oauth_file="/config/secrets/oauth.json",
                        cookies_file="/config/secrets/yt.txt",
                        pot_provider_url=f"http://127.0.0.1:{port}",
                    ),
                )
            ),
            config_root / "global.yaml",
        )

        body = _client(config_root).get("/api/alerts").json()

        assert not any(a["kind"] == "PO-token provider" for a in body["alerts"])
    finally:
        server.close()


def test_alerts_missing_pocketcasts_credentials_per_profile(config_root):
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")
    save_profile_config(_profile("bob"), config_root / "profiles" / "bob.yaml")

    body = _client(config_root).get("/api/alerts").json()

    pc_alerts = [a for a in body["alerts"] if a["kind"] == "Pocket Casts credentials"]
    assert {a["profile"] for a in pc_alerts} == {"alice", "bob"}
    assert all(a["severity"] == "missing" for a in pc_alerts)


def test_alerts_profile_on_global_default_not_duplicated(config_root):
    # alice has no override -- she shares the (missing) global Apple Music
    # file, already reported once at the global level; must not also get
    # her own "Apple Music cookies (override)" entry.
    save_profile_config(_profile("alice"), config_root / "profiles" / "alice.yaml")

    body = _client(config_root).get("/api/alerts").json()

    assert not any(a["kind"] == "Apple Music cookies (override)" for a in body["alerts"])
    assert sum(1 for a in body["alerts"] if a["kind"] == "Apple Music cookies") == 1


def test_alerts_profile_override_reported_separately(config_root):
    save_profile_config(
        _profile(
            "alice",
            sources=ProfileSourcesConfig(
                apple_music=ProfileAppleMusicOverride(
                    cookies_file="/config/secrets/alice/apple_music_cookies.txt"
                )
            ),
        ),
        config_root / "profiles" / "alice.yaml",
    )

    body = _client(config_root).get("/api/alerts").json()

    override_alert = next(
        a for a in body["alerts"] if a["kind"] == "Apple Music cookies (override)"
    )
    assert override_alert["profile"] == "alice"
    assert override_alert["severity"] == "missing"
    # The shared global default is still separately reported too.
    assert any(a["kind"] == "Apple Music cookies" and a["profile"] is None for a in body["alerts"])
