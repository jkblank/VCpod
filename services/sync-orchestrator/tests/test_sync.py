from pathlib import Path

import pytest
from common.models import (
    DeviceMatch,
    ExternalLibraryConfig,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SyncSettings,
)

from iopenpod.device.info import DeviceInfo

from sync_orchestrator import sync as sync_module
from sync_orchestrator.sync import (
    SyncError,
    _backup_progress_adapter,
    _capabilities_with_artwork_workaround,
    _engine_progress_adapter,
    _ThrottledProgressPrinter,
    plan_sync,
)


class _FakeDeviceInfo:
    def __init__(self, path: str):
        self.path = path


def _make_ipod_mount(tmp_path: Path) -> Path:
    # plan_sync's external_library check runs after resolve_itdb_path
    # succeeds, but before the iTunesDB is actually parsed — an empty
    # placeholder file is enough to get past that first existence check.
    mount = tmp_path / "ipod"
    itunes_dir = mount / "iPod_Control" / "iTunes"
    itunes_dir.mkdir(parents=True)
    (itunes_dir / "iTunesDB").write_bytes(b"")
    return mount


def _make_profile(tmp_path: Path, external_library_path: str) -> ProfileConfig:
    return ProfileConfig(
        profile="test",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(
            trigger="manual", transcode_format="mp3", push_play_status_back=False
        ),
        external_library=ExternalLibraryConfig(path=external_library_path, selections=[]),
    )


class _FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_throttled_progress_printer_always_emits_on_stage_change(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(sync_module.time, "monotonic", clock)
    messages: list[str] = []
    printer = _ThrottledProgressPrinter(messages.append, min_interval=100.0)

    printer.emit("scan", 1, 10, "a.mp3")
    printer.emit("backup", 1, 10, "b.mp3")

    assert messages == ["[scan] 1/10 — a.mp3", "[backup] 1/10 — b.mp3"]


def test_throttled_progress_printer_suppresses_rapid_same_stage_updates(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(sync_module.time, "monotonic", clock)
    messages: list[str] = []
    printer = _ThrottledProgressPrinter(messages.append, min_interval=1.0)

    printer.emit("scan", 1, 100, "a.mp3")
    printer.emit("scan", 2, 100, "b.mp3")
    printer.emit("scan", 3, 100, "c.mp3")

    assert messages == ["[scan] 1/100 — a.mp3"]


def test_throttled_progress_printer_emits_after_interval_elapses(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(sync_module.time, "monotonic", clock)
    messages: list[str] = []
    printer = _ThrottledProgressPrinter(messages.append, min_interval=1.0)

    printer.emit("scan", 1, 100, "a.mp3")
    clock.now = 1.5
    printer.emit("scan", 2, 100, "b.mp3")

    assert messages == ["[scan] 1/100 — a.mp3", "[scan] 2/100 — b.mp3"]


def test_throttled_progress_printer_always_emits_on_completion(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(sync_module.time, "monotonic", clock)
    messages: list[str] = []
    printer = _ThrottledProgressPrinter(messages.append, min_interval=100.0)

    printer.emit("scan", 1, 3, "a.mp3")
    printer.emit("scan", 3, 3, "c.mp3")

    assert messages == ["[scan] 1/3 — a.mp3", "[scan] 3/3 — c.mp3"]


def test_backup_progress_adapter_prefers_current_file_over_message():
    messages: list[str] = []
    from iopenpod.sync.backup_manager import BackupProgress

    on_progress = _backup_progress_adapter(messages.append)
    on_progress(BackupProgress("hashing", 1, 5, current_file="track.m4a", message="ignored"))

    assert messages == ["[hashing] 1/5 — track.m4a"]


def test_engine_progress_adapter_uses_stage_and_message():
    messages: list[str] = []
    from iopenpod.sync.core.models import EngineProgress

    on_progress = _engine_progress_adapter(messages.append)
    on_progress(EngineProgress(stage="scan", current=4, total=9, message="Scanning"))

    assert messages == ["[scan] 4/9 — Scanning"]


def test_plan_sync_raises_when_external_library_path_missing(tmp_path):
    mount = _make_ipod_mount(tmp_path)
    library_root = tmp_path / "library"
    (library_root / "music").mkdir(parents=True)
    (library_root / "playlists" / "test").mkdir(parents=True)
    state_root = tmp_path / "state"
    state_root.mkdir()

    profile = _make_profile(tmp_path, str(tmp_path / "does-not-exist"))

    with pytest.raises(SyncError, match="external_library path not found"):
        plan_sync(
            device_info=_FakeDeviceInfo(str(mount)),
            library_root=library_root,
            state_root=state_root,
            profile=profile,
        )


def test_capabilities_workaround_corrects_ipod_video_identity_and_finds_real_artwork_formats():
    # Real DeviceInfo, real (unmocked) capabilities_for_family_gen — this
    # is meant to prove iopenpod's own real table resolves correctly once
    # given the right identity, not just that our code calls some mock
    # the way we expect.
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "iPod Video"
    info.generation = ""

    capabilities = _capabilities_with_artwork_workaround(info)

    assert info.model_family == "iPod"
    assert info.generation == "5.5th Gen"
    assert capabilities.supports_artwork is True
    assert len(capabilities.cover_art_formats) > 0


def test_capabilities_workaround_falls_back_for_unrecognized_family():
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "Some Unknown Device"
    info.generation = ""

    capabilities = _capabilities_with_artwork_workaround(info)

    assert capabilities.supports_artwork is False
    # Identity is left alone for families this workaround doesn't know
    # anything special about.
    assert info.model_family == "Some Unknown Device"
