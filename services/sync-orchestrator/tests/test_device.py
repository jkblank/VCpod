from pathlib import Path

import pytest

import subprocess

from common.models import DeviceMatch
from sync_orchestrator import device as device_module
from sync_orchestrator.device import (
    DeviceNotFoundError,
    EjectError,
    eject_device,
    find_matching_device,
    is_ipod_mount,
    iter_candidate_mounts,
)


def _write_mounts(tmp_path: Path, lines: list[str]) -> Path:
    mounts_path = tmp_path / "mounts"
    mounts_path.write_text("\n".join(lines) + "\n")
    return mounts_path


def _make_ipod_mount(tmp_path: Path, name: str) -> Path:
    mount_point = tmp_path / name
    (mount_point / "iPod_Control" / "Device").mkdir(parents=True)
    (mount_point / "iPod_Control" / "Device" / "SysInfo").write_text("FirewireGuid: 0x1\n")
    return mount_point


class _FakeDeviceInfo:
    def __init__(self, path, serial="", firewire_guid=""):
        self.path = path
        self.serial = serial
        self.firewire_guid = firewire_guid
        self.model_family = "iPod Video"
        self.generation = "5.5th Gen"
        self.model_number = ""
        self.capacity = "160GB"


def test_iter_candidate_mounts_parses_vfat_and_hfsplus_only(tmp_path):
    mounts_path = _write_mounts(
        tmp_path,
        [
            "/dev/sda1 /boot/efi vfat rw,relatime 0 0",
            "/dev/sdb1 /mnt/data ext4 rw,relatime 0 0",
            "/dev/sdc1 /run/media/john/IPOD vfat rw,relatime 0 0",
        ],
    )
    candidates = iter_candidate_mounts(str(mounts_path))
    assert candidates == [
        ("/dev/sda1", "/boot/efi", "vfat"),
        ("/dev/sdc1", "/run/media/john/IPOD", "vfat"),
    ]


def test_iter_candidate_mounts_unescapes_spaces(tmp_path):
    # Confirmed live: /proc/mounts escapes spaces as \040 — a real mount
    # point like "JOHN'S IPOD" would otherwise be misparsed by a naive
    # whitespace split.
    mounts_path = _write_mounts(
        tmp_path,
        ["/dev/sdb2 /run/media/john/JOHN'S\\040IPOD vfat rw,relatime 0 0"],
    )
    candidates = iter_candidate_mounts(str(mounts_path))
    assert candidates == [("/dev/sdb2", "/run/media/john/JOHN'S IPOD", "vfat")]


def test_is_ipod_mount_true_for_real_ipod_structure(tmp_path):
    mount_point = _make_ipod_mount(tmp_path, "ipod")
    assert is_ipod_mount(str(mount_point)) is True


def test_is_ipod_mount_false_for_unreadable_mount(tmp_path):
    # Confirmed live: a real mount this user can't read (/boot/efi,
    # typically root-only) raises PermissionError from Path.is_file()
    # rather than returning False — must not crash the whole device scan
    # over one unrelated, inaccessible mount.
    restricted = tmp_path / "restricted"
    restricted.mkdir(mode=0o000)
    try:
        assert is_ipod_mount(str(restricted)) is False
    finally:
        restricted.chmod(0o755)  # allow tmp_path cleanup


def test_is_ipod_mount_false_for_unrelated_vfat_volume(tmp_path):
    # Confirmed live: /boot/efi is a real vfat mount on this machine with
    # no iPod_Control directory — must not false-positive.
    boot_efi = tmp_path / "boot_efi"
    boot_efi.mkdir()
    assert is_ipod_mount(str(boot_efi)) is False


def test_find_matching_device_by_volume_label(monkeypatch, tmp_path):
    ipod_mount = _make_ipod_mount(tmp_path, "ipod")
    other_mount = tmp_path / "boot_efi"
    other_mount.mkdir()

    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [
            ("/dev/sda1", str(other_mount), "vfat"),
            ("/dev/sdb2", str(ipod_mount), "vfat"),
        ],
    )
    monkeypatch.setattr(
        device_module,
        "read_volume_label",
        lambda block_device: {"/dev/sdb2": "JOHN'S IPOD"}.get(block_device, ""),
    )
    monkeypatch.setattr(
        device_module, "DeviceInfo", lambda path: _FakeDeviceInfo(path)
    )
    monkeypatch.setattr(device_module, "enrich", lambda info: None)

    match = DeviceMatch(match_by="volume_label", match_value="JOHN'S IPOD")
    info = find_matching_device(match)
    assert info.path == str(ipod_mount)


def test_find_matching_device_by_serial(monkeypatch, tmp_path):
    ipod_mount = _make_ipod_mount(tmp_path, "ipod")

    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdb2", str(ipod_mount), "vfat")],
    )
    monkeypatch.setattr(
        device_module,
        "DeviceInfo",
        lambda path: _FakeDeviceInfo(path, serial="AA11BB22"),
    )
    monkeypatch.setattr(device_module, "enrich", lambda info: None)

    match = DeviceMatch(match_by="serial", match_value="AA11BB22")
    info = find_matching_device(match)
    assert info.path == str(ipod_mount)
    assert info.serial == "AA11BB22"


def test_find_matching_device_raises_when_no_match(monkeypatch, tmp_path):
    ipod_mount = _make_ipod_mount(tmp_path, "ipod")

    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdb2", str(ipod_mount), "vfat")],
    )
    monkeypatch.setattr(device_module, "read_volume_label", lambda block_device: "SOMEONE_ELSES_IPOD")
    monkeypatch.setattr(
        device_module, "DeviceInfo", lambda path: _FakeDeviceInfo(path)
    )
    monkeypatch.setattr(device_module, "enrich", lambda info: None)

    match = DeviceMatch(match_by="volume_label", match_value="JOHN'S IPOD")
    with pytest.raises(DeviceNotFoundError):
        find_matching_device(match)


def test_find_matching_device_skips_non_ipod_vfat_mounts(monkeypatch, tmp_path):
    # A real, mounted, non-iPod vfat volume (like /boot/efi) with no
    # iPod_Control directory must be skipped entirely, never queried for
    # a label/serial match.
    other_mount = tmp_path / "boot_efi"
    other_mount.mkdir()

    def _fail_if_called(block_device):
        raise AssertionError("should not read label of a non-iPod mount")

    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sda1", str(other_mount), "vfat")],
    )
    monkeypatch.setattr(device_module, "read_volume_label", _fail_if_called)

    match = DeviceMatch(match_by="volume_label", match_value="JOHN'S IPOD")
    with pytest.raises(DeviceNotFoundError):
        find_matching_device(match)


class _FakeDeviceInfoForEject:
    def __init__(self, path):
        self.path = path


def test_eject_device_unmounts_then_powers_off_parent_drive(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdc2", "/run/media/john/JOHN_S IPOD", "vfat")],
    )
    calls = []

    def _fake_run(cmd, capture_output, text):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))

    assert calls == [
        ["udisksctl", "unmount", "-b", "/dev/sdc2"],
        ["udisksctl", "power-off", "-b", "/dev/sdc"],
    ]


def test_eject_device_raises_if_no_longer_mounted(monkeypatch):
    monkeypatch.setattr(device_module, "iter_candidate_mounts", lambda: [])

    with pytest.raises(EjectError, match="no longer mounted"):
        eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))


def test_eject_device_raises_on_unmount_failure(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdc2", "/run/media/john/JOHN_S IPOD", "vfat")],
    )

    def _fake_run(cmd, capture_output, text):
        if "unmount" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="target is busy")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(EjectError, match="unmount failed"):
        eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))


def test_eject_device_raises_on_power_off_failure(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdc2", "/run/media/john/JOHN_S IPOD", "vfat")],
    )

    def _fake_run(cmd, capture_output, text):
        if "power-off" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="device busy")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(EjectError, match="power-off failed"):
        eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))
