"""Real device discovery: finds a currently-mounted iPod matching a
profile's `device` config (common.models.DeviceMatch).

Assumes the device is already mounted (auto-mounted by the desktop
environment, as has been true throughout the M6 spike) — detecting a new
connection and mounting it is M9's job ("automation"), not this one.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from common.models import DeviceMatch, ProfileConfig
from iopenpod.device.info import DeviceInfo, enrich

_MOUNT_FSTYPES = ("vfat", "hfsplus")
_MOUNTS_PATH = "/proc/mounts"

# Matches a whole-disk device path off a partition path: /dev/sdc2 ->
# /dev/sdc, /dev/nvme0n1p2 -> /dev/nvme0n1 (iPods are always plain USB
# mass storage, so only the sdX form is ever hit live, but nvme is
# handled too rather than assuming one shape). Needed by eject_device —
# `udisksctl eject` operates on the whole drive, not a partition.
_PARENT_DRIVE_RE = re.compile(r"^(/dev/(?:[a-z]+|nvme\d+n\d+))p?\d+$")

# /proc/mounts escapes space/tab/newline/backslash in paths with octal
# codes — confirmed live these appear in real mount point names (a real
# volume label with a space, mounted at ".../JOHN'S IPOD" for example,
# would otherwise be misparsed by a naive whitespace split).
_MOUNT_ESCAPES = {
    "\\040": " ",
    "\\011": "\t",
    "\\012": "\n",
    "\\134": "\\",
}


class DeviceNotFoundError(Exception):
    pass


def _unescape_mount_path(raw: str) -> str:
    for escaped, char in _MOUNT_ESCAPES.items():
        raw = raw.replace(escaped, char)
    return raw


def iter_candidate_mounts(mounts_path: str = _MOUNTS_PATH) -> list[tuple[str, str, str]]:
    """Returns (block_device, mount_point, fstype) for every currently
    mounted vfat/hfsplus filesystem — the two real click-wheel iPod
    filesystem types, depending on generation/format."""
    candidates: list[tuple[str, str, str]] = []
    with open(mounts_path, encoding="utf-8") as f:
        for line in f:
            parts = line.split()
            if len(parts) < 3:
                continue
            device_path, mount_point, fstype = parts[0], parts[1], parts[2]
            if fstype in _MOUNT_FSTYPES:
                candidates.append((device_path, _unescape_mount_path(mount_point), fstype))
    return candidates


def _iter_unmounted_removable_partitions() -> list[str]:
    """Every vfat/hfsplus-formatted partition currently on the system
    that ISN'T already in /proc/mounts — via `lsblk`, which (unlike
    /proc/mounts) also sees partitions that exist but aren't mounted yet.
    Used by mount_candidate_devices() to find things worth auto-mounting."""
    result = subprocess.run(
        ["lsblk", "-rno", "PATH,FSTYPE"], capture_output=True, text=True, check=False
    )
    already_mounted = {device_path for device_path, _mount_point, _fstype in iter_candidate_mounts()}
    unmounted = []
    for line in result.stdout.splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        device_path, fstype = parts
        if fstype in _MOUNT_FSTYPES and device_path not in already_mounted:
            unmounted.append(device_path)
    return unmounted


def mount_candidate_devices() -> list[str]:
    """Best-effort auto-mount: mounts every currently-unmounted vfat/
    hfsplus partition via `udisksctl mount`, which auto-picks a sensible
    mount point (e.g. /run/media/<user>/<label>) — so find_matching_device
    has something to scan even when nothing has auto-mounted it yet.

    This exists for auto-sync specifically (confirmed live: a udev-
    triggered run has no guarantee any desktop session's auto-mount
    daemon actually mounts the device — unlike an interactive `sync`
    invocation, where a human has typically already seen the device
    appear in their file manager, mounted, by the time they run the
    command). `find_matching_device` itself still just scans whatever's
    mounted, same as before, for that reason — call this first if you
    can't rely on it already being mounted.

    Failures for individual devices are swallowed, not raised: an
    unrelated USB drive that can't be mounted (permissions, no udisks2
    policy for a headless/root context, corrupt filesystem, etc.) must
    never block finding the actual iPod. Returns the block devices this
    call actually mounted (for logging), not ones already mounted
    before it ran.
    """
    mounted: list[str] = []
    for block_device in _iter_unmounted_removable_partitions():
        result = subprocess.run(
            ["udisksctl", "mount", "-b", block_device], capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            mounted.append(block_device)
    return mounted


def is_ipod_mount(mount_point: str) -> bool:
    """Confirms a candidate mount is really an iPod, not an unrelated
    vfat/hfsplus volume (e.g. an EFI system partition — confirmed live
    this exact false-positive case exists on this machine). Some
    candidates (like /boot/efi, typically root-only readable) raise
    PermissionError from Path.is_file() rather than returning False —
    confirmed live — so this treats "can't even read it" the same as
    "not an iPod" instead of letting the error propagate and abort the
    whole device scan over one unrelated, inaccessible mount."""
    try:
        return (Path(mount_point) / "iPod_Control" / "Device" / "SysInfo").is_file()
    except OSError:
        return False


def read_volume_label(block_device: str) -> str:
    """Reads the real FAT volume label directly from the block device via
    `lsblk` — confirmed live this returns the true label (apostrophe
    intact) even when unmounted, unlike the mount point directory name,
    which udisks2 sanitizes (e.g. "JOHN'S IPOD" -> "JOHN_S IPOD") and
    would silently fail to match a profile's configured match_value."""
    result = subprocess.run(
        ["lsblk", "-no", "LABEL", block_device],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def find_matching_device(match: DeviceMatch) -> DeviceInfo:
    """Scans currently-mounted volumes for the one matching a profile's
    device config, returning a fully enriched DeviceInfo for it.

    Raises DeviceNotFoundError if no connected, mounted iPod matches.
    """
    for block_device, mount_point, _fstype in iter_candidate_mounts():
        if not is_ipod_mount(mount_point):
            continue

        if match.match_by == "volume_label":
            if read_volume_label(block_device) != match.match_value:
                continue
            info = DeviceInfo(path=mount_point)
            enrich(info)
            return info

        if match.match_by == "serial":
            info = DeviceInfo(path=mount_point)
            enrich(info)
            if match.match_value in (info.serial, info.firewire_guid):
                return info

    raise DeviceNotFoundError(
        f"no connected, mounted iPod matches {match.match_by}={match.match_value!r}"
    )


class AmbiguousDeviceMatchError(Exception):
    pass


def find_matching_profile(profiles: list[ProfileConfig]) -> ProfileConfig:
    """M9's udev-triggered auto-sync doesn't know in advance which profile
    a newly-connected device belongs to (unlike `sync-orchestrator sync`,
    which always takes an explicit --profile) — this determines it by
    trying find_matching_device(profile.device) per profile, reusing that
    scan/match logic as-is rather than reimplementing it.

    Raises DeviceNotFoundError if no profile's device config matches
    whatever's currently connected. Raises AmbiguousDeviceMatchError if
    more than one profile matches — almost certainly a config bug (e.g.
    two profiles with the same device.match_value), which must be
    surfaced loudly rather than silently syncing the wrong profile's data
    onto someone's iPod.
    """
    matches: list[ProfileConfig] = []
    last_error: DeviceNotFoundError | None = None
    for profile in profiles:
        try:
            find_matching_device(profile.device)
        except DeviceNotFoundError as e:
            last_error = e
            continue
        matches.append(profile)

    if not matches:
        raise DeviceNotFoundError(str(last_error) if last_error else "no profiles configured")
    if len(matches) > 1:
        names = ", ".join(p.profile for p in matches)
        raise AmbiguousDeviceMatchError(
            f"connected device matches multiple profiles: {names} — check "
            "config/profiles/*.yaml for a duplicate/incorrect device.match_value"
        )
    return matches[0]


class EjectError(Exception):
    pass


def eject_device(device_info: DeviceInfo) -> None:
    """Ejects the drive via `udisksctl eject` (UDisks2's `Drive.Eject()`),
    the exact call a desktop file manager's own eject button makes —
    confirmed live by eavesdropping the real D-Bus traffic with `busctl
    monitor org.freedesktop.UDisks2` while triggering a GUI eject: it's a
    single `Drive.Eject()` call, nothing else (no separate `Unmount`, no
    `PowerOff`). The resulting `PropertiesChanged` signal sets the
    drive's `MediaAvailable=False`/`Size=0` — a SCSI/media-layer "the
    media is gone" signal, which is what gets the iPod out of "connected
    to computer" mode — without touching the USB port's power state at
    all, unlike `Drive.PowerOff()` (an earlier, wrong attempt at this:
    deauthorizes/powers down the port electrically, which stopped the
    device charging). `Eject()` handles unmounting any mounted
    filesystems on the drive internally — no separate `udisksctl
    unmount` call needed first (also confirmed live: the real capture
    shows no Unmount call at all, only Eject)."""
    block_device = None
    for candidate_device, mount_point, _fstype in iter_candidate_mounts():
        if mount_point == device_info.path:
            block_device = candidate_device
            break
    if block_device is None:
        raise EjectError(f"device no longer mounted at {device_info.path!r}; can't eject")

    match = _PARENT_DRIVE_RE.match(block_device)
    if not match:
        raise EjectError(f"could not determine parent drive for {block_device!r}")
    drive = match.group(1)

    eject = subprocess.run(["udisksctl", "eject", "-b", drive], capture_output=True, text=True)
    if eject.returncode != 0:
        raise EjectError(f"udisksctl eject failed: {eject.stdout}{eject.stderr}")
