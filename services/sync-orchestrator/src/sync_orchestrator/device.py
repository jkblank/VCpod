"""Real device discovery: finds a currently-mounted iPod matching a
profile's `device` config (common.models.DeviceMatch).

Assumes the device is already mounted (auto-mounted by the desktop
environment, as has been true throughout the M6 spike) — detecting a new
connection and mounting it is M9's job ("automation"), not this one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from common.models import DeviceMatch
from iopenpod.device.info import DeviceInfo, enrich

_MOUNT_FSTYPES = ("vfat", "hfsplus")
_MOUNTS_PATH = "/proc/mounts"

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
