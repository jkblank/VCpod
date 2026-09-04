from pathlib import Path

import pytest

import subprocess

from common.models import (
    DeviceMatch,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SyncSettings,
)
from sync_orchestrator import device as device_module
from sync_orchestrator.device import (
    AmbiguousDeviceMatchError,
    DeviceNotFoundError,
    EjectError,
    _disk_usage,
    eject_device,
    find_matching_device,
    find_matching_profile,
    is_ipod_mount,
    iter_candidate_mounts,
    iter_connected_devices,
    mount_candidate_devices,
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


def test_is_ipod_mount_true_for_rockbox_only_structure(tmp_path):
    # A device with a .rockbox install but no iPod_Control at all (not
    # yet confirmed against a real device — see the "Rockbox support"
    # plan's open questions) must still be recognized.
    rockbox_mount = tmp_path / "rockbox_ipod"
    (rockbox_mount / ".rockbox").mkdir(parents=True)
    assert is_ipod_mount(str(rockbox_mount)) is True


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


def test_iter_connected_devices_returns_identity_for_each_mounted_ipod(monkeypatch, tmp_path):
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
        device_module,
        "DeviceInfo",
        lambda path: _FakeDeviceInfo(path, serial="AA11BB22"),
    )
    monkeypatch.setattr(device_module, "enrich", lambda info: None)

    devices = iter_connected_devices()

    # The unrelated boot_efi vfat mount must never appear -- same
    # is_ipod_mount gate find_matching_device uses.
    assert len(devices) == 1
    device = devices[0]
    assert device.path == str(ipod_mount)
    assert device.volume_label == "JOHN'S IPOD"
    assert device.serial == "AA11BB22"
    assert device.model_family == "iPod Video"
    assert device.generation == "5.5th Gen"
    assert device.capacity == "160GB"
    # Real shutil.disk_usage() against the real tmp_path filesystem --
    # can't assert exact values, but confirms it's a real measurement,
    # not a stub/zero.
    assert device.used_bytes > 0
    assert device.free_bytes > 0


def test_disk_usage_real_path_returns_positive_values(tmp_path):
    used, free = _disk_usage(str(tmp_path))
    assert used > 0
    assert free > 0


def test_disk_usage_missing_path_returns_zero_not_raise(tmp_path):
    used, free = _disk_usage(str(tmp_path / "does-not-exist"))
    assert (used, free) == (0, 0)


def test_iter_connected_devices_returns_empty_list_when_nothing_connected(monkeypatch):
    monkeypatch.setattr(device_module, "iter_candidate_mounts", lambda: [])

    assert iter_connected_devices() == []


class _FakeDeviceInfoForEject:
    def __init__(self, path):
        self.path = path


def test_eject_device_calls_eject_on_parent_drive(monkeypatch):
    # Confirmed live via `busctl monitor org.freedesktop.UDisks2` while
    # triggering a real GUI eject: a file manager's eject button makes a
    # single Drive.Eject() call — not Unmount, not PowerOff. PowerOff (an
    # earlier, wrong attempt at this) deauthorizes/powers down the USB
    # port electrically, which stops the device charging; Eject only
    # marks the media logically gone, which is what gets the iPod out of
    # "connected to computer" mode without touching port power.
    # `udisksctl` would be the direct way to invoke that same D-Bus
    # method, but confirmed live: this system's udisksctl CLI has no
    # `eject` verb at all (only mount/unmount/power-off/...), so the
    # classic standalone `eject` utility (util-linux) is used instead.
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdc2", "/run/media/john/JOHN_S IPOD", "vfat")],
    )
    calls = []

    def _fake_run(cmd, capture_output, text, check=False):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))

    assert calls == [["eject", "/dev/sdc"]]


def test_eject_device_raises_if_no_longer_mounted(monkeypatch):
    monkeypatch.setattr(device_module, "iter_candidate_mounts", lambda: [])

    with pytest.raises(EjectError, match="no longer mounted"):
        eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))


def test_eject_device_raises_on_eject_failure(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdc2", "/run/media/john/JOHN_S IPOD", "vfat")],
    )

    def _fake_run(cmd, capture_output, text, check=False):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="target is busy")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    with pytest.raises(EjectError, match="eject failed"):
        eject_device(_FakeDeviceInfoForEject("/run/media/john/JOHN_S IPOD"))


# --- find_matching_profile ---------------------------------------------------


def _profile(name: str, match_value: str) -> ProfileConfig:
    return ProfileConfig(
        profile=name,
        device=DeviceMatch(match_by="volume_label", match_value=match_value),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )


def test_find_matching_profile_returns_the_one_matching_profile(monkeypatch):
    alice = _profile("alice", "ALICE_IPOD")
    bob = _profile("bob", "BOB_IPOD")

    def _fake_find(match):
        if match.match_value != "BOB_IPOD":
            raise DeviceNotFoundError(f"no match for {match.match_value!r}")
        return object()

    monkeypatch.setattr(device_module, "find_matching_device", _fake_find)

    assert find_matching_profile([alice, bob]) is bob


def test_find_matching_profile_raises_device_not_found_when_none_match(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "find_matching_device",
        lambda match: (_ for _ in ()).throw(DeviceNotFoundError("nope")),
    )

    with pytest.raises(DeviceNotFoundError):
        find_matching_profile([_profile("alice", "ALICE_IPOD"), _profile("bob", "BOB_IPOD")])


def test_find_matching_profile_raises_device_not_found_for_empty_profile_list():
    with pytest.raises(DeviceNotFoundError, match="no profiles configured"):
        find_matching_profile([])


def test_find_matching_profile_raises_ambiguous_when_two_profiles_both_match(monkeypatch):
    monkeypatch.setattr(device_module, "find_matching_device", lambda match: object())

    with pytest.raises(AmbiguousDeviceMatchError, match="alice, bob"):
        find_matching_profile([_profile("alice", "SAME"), _profile("bob", "SAME")])


# --- mount_candidate_devices --------------------------------------------------


def test_mount_candidate_devices_mounts_each_unmounted_vfat_hfsplus_partition(monkeypatch):
    monkeypatch.setattr(device_module, "iter_candidate_mounts", lambda: [])

    def _fake_run(cmd, capture_output, text, check=False):
        if cmd[0] == "lsblk":
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="/dev/sda \n/dev/sdb1 vfat\n/dev/sdb2 btrfs\n/dev/sdc1 hfsplus\n",
                stderr="",
            )
        assert cmd[:2] == ["udisksctl", "mount"]
        return subprocess.CompletedProcess(cmd, 0, stdout="Mounted", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    mounted = mount_candidate_devices()

    # /dev/sda (no fstype) and /dev/sdb2 (btrfs) are correctly excluded —
    # only real iPod-shaped filesystems get auto-mounted.
    assert sorted(mounted) == ["/dev/sdb1", "/dev/sdc1"]


def test_mount_candidate_devices_skips_already_mounted_partitions(monkeypatch):
    monkeypatch.setattr(
        device_module,
        "iter_candidate_mounts",
        lambda: [("/dev/sdb1", "/run/media/john/IPOD", "vfat")],
    )
    calls = []

    def _fake_run(cmd, capture_output, text, check=False):
        if cmd[0] == "lsblk":
            return subprocess.CompletedProcess(cmd, 0, stdout="/dev/sdb1 vfat\n", stderr="")
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    mounted = mount_candidate_devices()

    assert calls == []  # udisksctl never invoked — already mounted
    assert mounted == []


def test_mount_candidate_devices_swallows_failure_for_one_device(monkeypatch):
    monkeypatch.setattr(device_module, "iter_candidate_mounts", lambda: [])

    def _fake_run(cmd, capture_output, text, check=False):
        if cmd[0] == "lsblk":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="/dev/sdb1 vfat\n/dev/sdc1 hfsplus\n", stderr=""
            )
        if cmd[3] == "/dev/sdb1":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="not authorized")
        return subprocess.CompletedProcess(cmd, 0, stdout="Mounted", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    mounted = mount_candidate_devices()

    # sdb1 failed to mount — swallowed, not raised — but sdc1 still
    # succeeds; a stuck/unrelated device must never block the real iPod.
    assert mounted == ["/dev/sdc1"]
