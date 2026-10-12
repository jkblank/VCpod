from datetime import datetime

from fastapi.testclient import TestClient

from fetch_scheduler.loop import TickResult
from web_gui_backend import routers
from web_gui_backend.app import create_app


def _client(tmp_path) -> TestClient:
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    (config_root / "profiles" / "john.yaml").write_text(
        "profile: john\ndevice:\n  match_by: serial\n  match_value: X\n"
        "playlists: []\npodcasts:\n  pocketcasts:\n    credentials_file: /x.json\n"
        "  sync_unplayed_only: true\n  max_episodes_per_show: 5\n  shows: all\n"
        "sync:\n  trigger: manual\n  transcode_format: alac\n  push_play_status_back: false\n"
    )
    return TestClient(create_app(config_root=config_root, library_root=tmp_path / "library",
                                 state_root=tmp_path / "state"))


def test_fetch_now_404s_for_unknown_profile(tmp_path):
    resp = _client(tmp_path).post("/api/profiles/nobody/fetch-now")
    assert resp.status_code == 404


def test_fetch_now_returns_fetched_targets_and_errors(monkeypatch, tmp_path):
    def fake_fetch_profile_now(**kwargs):
        result = TickResult()
        result.fetched["john"] = ["Chill", "__all__"]
        result.source_errors["john"] = ["apple_music: expired cookies"]
        return result

    monkeypatch.setattr(routers.fetch_now, "fetch_profile_now", fake_fetch_profile_now)

    resp = _client(tmp_path).post("/api/profiles/john/fetch-now")

    assert resp.status_code == 200
    assert resp.json() == {
        "fetched": ["Chill", "__all__"],
        "source_errors": ["apple_music: expired cookies"],
        "errors": [],
    }
