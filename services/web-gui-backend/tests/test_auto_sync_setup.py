from fastapi.testclient import TestClient

from web_gui_backend.app import create_app


def _client(tmp_path) -> TestClient:
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    library_root = tmp_path / "library"
    state_root = tmp_path / "state"
    app = create_app(
        config_root=config_root,
        library_root=library_root,
        state_root=state_root,
        sync_orchestrator_dir=tmp_path / "sync-orchestrator-project",
    )
    return TestClient(app)


def test_generates_systemd_unit_with_real_paths(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/auto-sync/setup")

    assert resp.status_code == 200
    body = resp.json()
    unit = body["systemd_unit"]
    assert str(tmp_path / "sync-orchestrator-project" / ".venv" / "bin" / "sync-orchestrator") in unit
    assert f"--config-root {tmp_path / 'config'}" in unit
    assert f"--library-root {tmp_path / 'library'}" in unit
    assert f"--state-root {tmp_path / 'state'}" in unit
    assert str(tmp_path / "state" / "auto-sync.log") in unit
    assert "User=root" in unit


def test_generates_udev_rule_with_confirmed_vid_pid_and_caveat(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/auto-sync/setup")

    rule = resp.json()["udev_rule"]
    assert 'ATTR{idVendor}=="05ac"' in rule
    assert 'ATTR{idProduct}=="1209"' in rule
    assert "KNOWN LIMITATION" in rule
    assert "lsusb" in rule


def test_writes_generated_files_under_state_root(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/auto-sync/setup")

    body = resp.json()
    unit_path = tmp_path / "state" / "generated" / "music-stack-auto-sync.service"
    rule_path = tmp_path / "state" / "generated" / "99-ipod-music-stack.rules"
    assert unit_path.read_text() == body["systemd_unit"]
    assert rule_path.read_text() == body["udev_rule"]


def test_install_commands_reference_the_generated_files(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/auto-sync/setup")

    commands = resp.json()["install_commands"]
    joined = "\n".join(commands)
    assert str(tmp_path / "state" / "generated" / "music-stack-auto-sync.service") in joined
    assert "/etc/systemd/system/music-stack-auto-sync.service" in joined
    assert str(tmp_path / "state" / "generated" / "99-ipod-music-stack.rules") in joined
    assert "/etc/udev/rules.d/99-ipod-music-stack.rules" in joined
    assert any("daemon-reload" in c for c in commands)
    assert any("udevadm control --reload-rules" in c for c in commands)
    assert all(c.startswith("sudo ") for c in commands)


def test_status_reports_real_install_state(tmp_path, monkeypatch):
    client = _client(tmp_path)
    from web_gui_backend.routers import auto_sync_setup as router_module

    monkeypatch.setattr(router_module, "_SYSTEMD_UNIT_INSTALL_PATH", tmp_path / "not-there.service")
    monkeypatch.setattr(router_module, "_UDEV_RULE_INSTALL_PATH", tmp_path / "not-there.rules")

    resp = client.get("/api/auto-sync/setup")

    assert resp.json()["status"] == {"systemd_installed": False, "udev_rule_installed": False}


def test_status_reflects_a_file_that_actually_exists(tmp_path, monkeypatch):
    client = _client(tmp_path)
    from web_gui_backend.routers import auto_sync_setup as router_module

    fake_unit = tmp_path / "already-installed.service"
    fake_unit.write_text("fake")
    monkeypatch.setattr(router_module, "_SYSTEMD_UNIT_INSTALL_PATH", fake_unit)
    monkeypatch.setattr(router_module, "_UDEV_RULE_INSTALL_PATH", tmp_path / "not-there.rules")

    resp = client.get("/api/auto-sync/setup")

    assert resp.json()["status"] == {"systemd_installed": True, "udev_rule_installed": False}
