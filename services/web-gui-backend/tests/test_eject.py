import subprocess

from fastapi.testclient import TestClient

from web_gui_backend import routers
from web_gui_backend.app import create_app


def _client(tmp_path) -> TestClient:
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    (config_root / "profiles" / "john.yaml").write_text("profile: john\n")
    return TestClient(create_app(config_root=config_root, library_root=tmp_path / "library",
                                 state_root=tmp_path / "state"))


def test_eject_404s_for_unknown_profile(tmp_path):
    assert _client(tmp_path).post("/api/profiles/nobody/eject").status_code == 404


def test_eject_refuses_while_a_sync_is_running(monkeypatch, tmp_path):
    monkeypatch.setattr(routers.eject, "is_sync_running", lambda state_root, name: True)

    resp = _client(tmp_path).post("/api/profiles/john/eject")

    assert resp.status_code == 409
    assert "sync is running" in resp.json()["detail"]


def test_eject_runs_sync_orchestrator_and_reports_success(monkeypatch, tmp_path):
    monkeypatch.setattr(routers.eject, "is_sync_running", lambda state_root, name: False)
    captured = {}

    def _fake_run(cmd, capture_output, text, check):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="Device ejected — safe to disconnect.\n", stderr="")

    monkeypatch.setattr(routers.eject.subprocess, "run", _fake_run)

    resp = _client(tmp_path).post("/api/profiles/john/eject")

    assert resp.status_code == 200
    assert resp.json() == {"ejected": True, "message": "Device ejected — safe to disconnect."}
    assert captured["cmd"][-4:-1] == ["sync-orchestrator", "eject", "--profile"]


def test_eject_reports_failure_when_no_device_connected(monkeypatch, tmp_path):
    monkeypatch.setattr(routers.eject, "is_sync_running", lambda state_root, name: False)
    monkeypatch.setattr(
        routers.eject.subprocess, "run",
        lambda cmd, capture_output, text, check: subprocess.CompletedProcess(cmd, 1, stdout="FAIL: no device\n", stderr=""),
    )

    resp = _client(tmp_path).post("/api/profiles/john/eject")

    assert resp.status_code == 409
    assert resp.json()["detail"] == "FAIL: no device"
