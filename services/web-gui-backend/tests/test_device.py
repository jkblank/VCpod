import subprocess
from types import SimpleNamespace

import pytest

from web_gui_backend import device as device_module
from web_gui_backend.device import DeviceIdentifyError, identify_connected_devices


def _fake_run(returncode=0, stdout="", stderr=""):
    return lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_identify_connected_devices_parses_real_shape(monkeypatch):
    payload = (
        '{"devices": [{"path": "/mnt/ipod", "volume_label": "JOHN\'S IPOD", '
        '"serial": "AA11BB22", "firewire_guid": "", "model_family": "iPod Video", '
        '"generation": "5.5th Gen", "model_number": "MA450", "capacity": "80GB"}]}'
    )
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout=payload))

    devices = identify_connected_devices("services/sync-orchestrator")

    assert devices == [
        {
            "path": "/mnt/ipod",
            "volume_label": "JOHN'S IPOD",
            "serial": "AA11BB22",
            "firewire_guid": "",
            "model_family": "iPod Video",
            "generation": "5.5th Gen",
            "model_number": "MA450",
            "capacity": "80GB",
        }
    ]


def test_identify_connected_devices_empty_when_nothing_connected(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout='{"devices": []}'))

    assert identify_connected_devices("services/sync-orchestrator") == []


def test_identify_connected_devices_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(returncode=1, stderr="boom"))

    with pytest.raises(DeviceIdentifyError, match="boom"):
        identify_connected_devices("services/sync-orchestrator")


def test_identify_connected_devices_raises_on_unparseable_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run(stdout="not json"))

    with pytest.raises(DeviceIdentifyError):
        identify_connected_devices("services/sync-orchestrator")


def test_identify_connected_devices_builds_expected_command(monkeypatch):
    captured = {}

    def _capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout='{"devices": []}', stderr="")

    monkeypatch.setattr(subprocess, "run", _capture_run)

    identify_connected_devices("services/sync-orchestrator")

    assert captured["cmd"] == [
        "uv", "run", "--project", "services/sync-orchestrator",
        "sync-orchestrator", "identify-device", "--no-mount",
    ]


def test_identify_connected_devices_defaults_project_dir_to_sibling(monkeypatch):
    captured = {}

    def _capture_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout='{"devices": []}', stderr="")

    monkeypatch.setattr(subprocess, "run", _capture_run)

    identify_connected_devices()

    assert captured["cmd"][3].endswith("services/sync-orchestrator")


def test_identify_connected_devices_never_mounts_by_default(monkeypatch):
    # Regression: background polling used to run identify-device with
    # auto-mount, so the web GUI could mount an iPod while a sync was
    # backing it up -- the safety check then aborted with "filesystem
    # changed while its backup was being created". See notes.md.
    import subprocess

    from web_gui_backend import device as device_module

    seen = []

    def _fake_run(cmd, capture_output, text, check):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='{"devices": []}', stderr="")

    monkeypatch.setattr(device_module.subprocess, "run", _fake_run)

    device_module.identify_connected_devices("/sync")
    device_module.identify_connected_devices("/sync", mount=True)

    assert seen[0][-1] == "--no-mount"
    assert "--no-mount" not in seen[1]
