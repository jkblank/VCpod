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


def test_host_overrides_are_used_instead_of_this_process_own_paths(tmp_path):
    # Regression: a containerized web-gui-backend's own config_root/
    # library_root/state_root/sync_orchestrator_dir are that container's
    # bind-mount targets (e.g. /config), meaningless to a systemd unit
    # that always runs on the bare host -- confirmed live, the generated
    # unit tried to exec the container's own vendored sync-orchestrator
    # binary and failed on every trigger. See notes.md's 2026-09-29
    # entry.
    config_root = tmp_path / "container-config"
    (config_root / "profiles").mkdir(parents=True)
    app = create_app(
        config_root=config_root,
        library_root=tmp_path / "container-library",
        state_root=tmp_path / "container-state",
        sync_orchestrator_dir=tmp_path / "container-sync-orchestrator",
        host_config_root=tmp_path / "host" / "config",
        host_library_root=tmp_path / "host" / "library",
        host_state_root=tmp_path / "host" / "state",
        host_sync_orchestrator_dir=tmp_path / "host" / "sync-orchestrator",
    )
    client = TestClient(app)

    resp = client.get("/api/auto-sync/setup")

    unit = resp.json()["systemd_unit"]
    assert str(tmp_path / "host" / "sync-orchestrator" / ".venv" / "bin" / "sync-orchestrator") in unit
    assert f"--config-root {tmp_path / 'host' / 'config'}" in unit
    assert f"--library-root {tmp_path / 'host' / 'library'}" in unit
    assert f"--state-root {tmp_path / 'host' / 'state'}" in unit
    assert str(tmp_path / "host" / "state" / "auto-sync.log") in unit
    assert "container-config" not in unit
    assert "container-library" not in unit
    assert "container-state" not in unit
    assert "container-sync-orchestrator" not in unit


def test_writes_to_this_process_own_state_root_even_with_host_override(tmp_path):
    # Regression: the fix above (host_* overrides feeding the generated
    # unit's *text*) was itself buggy -- it also fed host_state_root into
    # the path this process uses to *write* the generated files to its
    # own disk. A containerized process can only really write through
    # its own filesystem view (state.state_root); writing through the
    # host-facing path string instead silently lands in a phantom
    # directory that happens to share that path inside the container's
    # own writable layer, never reaching the real bind-mounted location
    # the install_commands' `sudo cp` actually reads from on the host.
    # Confirmed live: every previous verification call "succeeded" and
    # returned fresh content, but the real file on the host's disk never
    # changed. See notes.md's 2026-09-29 entry (second same-day one).
    config_root = tmp_path / "container-config"
    (config_root / "profiles").mkdir(parents=True)
    container_state = tmp_path / "container-state"
    app = create_app(
        config_root=config_root,
        library_root=tmp_path / "container-library",
        state_root=container_state,
        sync_orchestrator_dir=tmp_path / "container-sync-orchestrator",
        host_config_root=tmp_path / "host" / "config",
        host_library_root=tmp_path / "host" / "library",
        host_state_root=tmp_path / "host" / "state",
        host_sync_orchestrator_dir=tmp_path / "host" / "sync-orchestrator",
    )
    client = TestClient(app)

    resp = client.get("/api/auto-sync/setup")

    body = resp.json()
    written_unit = container_state / "generated" / "music-stack-auto-sync.service"
    written_rule = container_state / "generated" / "99-ipod-music-stack.rules"
    assert written_unit.read_text() == body["systemd_unit"]
    assert written_rule.read_text() == body["udev_rule"]
    assert not (tmp_path / "host" / "state" / "generated").exists()

    # install_commands still show the HOST-visible path (for the human
    # running sudo cp on bare metal), even though the write above went
    # through the container-local path.
    joined = "\n".join(body["install_commands"])
    assert str(tmp_path / "host" / "state" / "generated" / "music-stack-auto-sync.service") in joined
    assert str(tmp_path / "host" / "state" / "generated" / "99-ipod-music-stack.rules") in joined


def test_status_reflects_a_file_that_actually_exists(tmp_path, monkeypatch):
    client = _client(tmp_path)
    from web_gui_backend.routers import auto_sync_setup as router_module

    fake_unit = tmp_path / "already-installed.service"
    fake_unit.write_text("fake")
    monkeypatch.setattr(router_module, "_SYSTEMD_UNIT_INSTALL_PATH", fake_unit)
    monkeypatch.setattr(router_module, "_UDEV_RULE_INSTALL_PATH", tmp_path / "not-there.rules")

    resp = client.get("/api/auto-sync/setup")

    assert resp.json()["status"] == {"systemd_installed": True, "udev_rule_installed": False}
