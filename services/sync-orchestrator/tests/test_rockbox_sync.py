from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from common.models import (
    AudiobooksConfig,
    DeviceMatch,
    ExternalLibraryConfig,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    SyncSettings,
)
from common.state import EpisodeRecord, StateDB

from sync_orchestrator.rockbox_sync import (
    RockboxSyncError,
    RockboxSyncItem,
    _diff_plan,
    _mtime_matches,
    _render_m3u8_relative,
    _select_podcast_episode_files,
    _transcode_options_for,
    _walk_managed_device_files,
    plan_rockbox_sync,
)


def _make_profile(
    tmp_path: Path,
    *,
    playlists: list | None = None,
    transcode_format: str = "alac",
    podcasts: bool = True,
) -> ProfileConfig:
    return ProfileConfig(
        profile="test",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=playlists or [],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=2,
        ),
        sync=SyncSettings(
            trigger="manual", transcode_format=transcode_format, push_play_status_back=False,
            mode="rockbox",
        ),
    )


class _FakeDeviceInfo:
    def __init__(self, path: str):
        self.path = path
        self.serial = "FAKESERIAL"
        self.firewire_guid = None
        self.ipod_name = "Fake Rockbox iPod"
        self.reported_volume_format = "vfat"
        self.volume_identity_key = "key"


# --- _transcode_options_for --------------------------------------------------


def test_transcode_options_for_alac_prefers_lossless(tmp_path):
    profile = _make_profile(tmp_path, transcode_format="alac")
    assert _transcode_options_for(profile).prefer_lossy is False


def test_transcode_options_for_aac_prefers_lossy(tmp_path):
    profile = _make_profile(tmp_path, transcode_format="aac")
    assert _transcode_options_for(profile).prefer_lossy is True


def test_transcode_options_for_unknown_format_raises(tmp_path):
    profile = _make_profile(tmp_path, transcode_format="mp3")
    with pytest.raises(RockboxSyncError, match="transcode_format"):
        _transcode_options_for(profile)


# --- _mtime_matches / _diff_plan ---------------------------------------------


def test_mtime_matches_within_fat32_tolerance():
    assert _mtime_matches(100.0, 101.5) is True


def test_mtime_matches_outside_tolerance():
    assert _mtime_matches(100.0, 105.0) is False


def _item(device_relative: str, source_path: str) -> RockboxSyncItem:
    from iopenpod.sync.transcoder import TranscodePlan, TranscodeTarget

    plan = TranscodePlan(
        source_path=Path(source_path),
        target=TranscodeTarget.COPY,
        aac_quality="normal",
        effective_quality="normal",
        prefer_lossy=False,
        normalize_sample_rate=False,
        mono_for_spoken=False,
        smart_quality_by_type=False,
        video_crf=23,
        video_preset="medium",
        video_max_width=0,
        video_max_height=0,
        video_max_fps=0,
        video_max_bitrate_kbps=0,
        video_h264_level="3.0",
    )
    return RockboxSyncItem(device_relative_path=device_relative, source_path=source_path, transcode_plan=plan)


def test_diff_plan_new_file_goes_to_add(tmp_path):
    source = tmp_path / "a.m4a"
    source.write_bytes(b"x")
    desired = {"Music/a.m4a": _item("Music/a.m4a", str(source))}

    plan = _diff_plan(desired, existing={})

    assert plan.to_add == [desired["Music/a.m4a"]]
    assert plan.to_update == []


def test_diff_plan_matching_mtime_is_a_no_op(tmp_path):
    source = tmp_path / "a.m4a"
    source.write_bytes(b"x")
    mtime = source.stat().st_mtime
    desired = {"Music/a.m4a": _item("Music/a.m4a", str(source))}

    plan = _diff_plan(desired, existing={"Music/a.m4a": mtime})

    assert plan.to_add == []
    assert plan.to_update == []


def test_diff_plan_changed_mtime_goes_to_update(tmp_path):
    source = tmp_path / "a.m4a"
    source.write_bytes(b"x")
    desired = {"Music/a.m4a": _item("Music/a.m4a", str(source))}

    plan = _diff_plan(desired, existing={"Music/a.m4a": source.stat().st_mtime - 100})

    assert plan.to_add == []
    assert plan.to_update == [desired["Music/a.m4a"]]


# --- _walk_managed_device_files ----------------------------------------------


def test_walk_managed_device_files_only_walks_known_dirnames(tmp_path):
    device_root = tmp_path / "ipod"
    (device_root / "Music" / "Artist").mkdir(parents=True)
    (device_root / "Music" / "Artist" / "Track.m4a").write_bytes(b"x")
    (device_root / "iPod_Control" / "Device").mkdir(parents=True)
    (device_root / "iPod_Control" / "Device" / "SysInfo").write_bytes(b"x")
    (device_root / ".rockbox").mkdir()
    (device_root / ".rockbox" / "config.cfg").write_bytes(b"x")

    existing = _walk_managed_device_files(device_root)

    assert existing == {"Music/Artist/Track.m4a": pytest.approx(
        (device_root / "Music" / "Artist" / "Track.m4a").stat().st_mtime
    )}


# --- _render_m3u8_relative ----------------------------------------------------


def test_render_m3u8_relative_from_playlists_dir():
    content = _render_m3u8_relative(
        "Playlists/Chill.m3u8", ["Music/Artist/Track.m4a", "Music/Other/Song.mp3"]
    )
    lines = content.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "../Music/Artist/Track.m4a"
    assert lines[2] == "../Music/Other/Song.mp3"


# --- _select_podcast_episode_files -------------------------------------------


def test_select_podcast_episode_files_excludes_played_when_unplayed_only(tmp_path):
    library_root = tmp_path / "library"
    podcasts_dir = library_root / "podcasts" / "Show"
    podcasts_dir.mkdir(parents=True)
    (podcasts_dir / "played.mp3").write_bytes(b"x")
    (podcasts_dir / "unplayed.mp3").write_bytes(b"x")

    state_db_path = tmp_path / "state" / "test.sqlite"
    with StateDB(state_db_path) as db:
        db.record_episode(EpisodeRecord(
            episode_uuid="played-1", podcast_uuid="show-1", show_name="Show",
            local_path=str(podcasts_dir / "played.mp3"), played=True, played_up_to=100,
            downloaded_at="2026-01-01T00:00:00Z", published_at="2026-01-02T00:00:00Z",
        ))
        db.record_episode(EpisodeRecord(
            episode_uuid="unplayed-1", podcast_uuid="show-1", show_name="Show",
            local_path=str(podcasts_dir / "unplayed.mp3"), played=False, played_up_to=0,
            downloaded_at="2026-01-01T00:00:00Z", published_at="2026-01-01T00:00:00Z",
        ))

    profile = _make_profile(tmp_path)
    selected = _select_podcast_episode_files(state_db_path, library_root, profile)

    assert selected == [podcasts_dir / "unplayed.mp3"]


def test_select_podcast_episode_files_caps_at_max_episodes_per_show_newest_first(tmp_path):
    library_root = tmp_path / "library"
    podcasts_dir = library_root / "podcasts" / "Show"
    podcasts_dir.mkdir(parents=True)
    for i in range(3):
        (podcasts_dir / f"ep{i}.mp3").write_bytes(b"x")

    state_db_path = tmp_path / "state" / "test.sqlite"
    with StateDB(state_db_path) as db:
        for i in range(3):
            db.record_episode(EpisodeRecord(
                episode_uuid=f"ep-{i}", podcast_uuid="show-1", show_name="Show",
                local_path=str(podcasts_dir / f"ep{i}.mp3"), played=False, played_up_to=0,
                downloaded_at="2026-01-01T00:00:00Z",
                published_at=f"2026-01-0{i + 1}T00:00:00Z",
            ))

    profile = _make_profile(tmp_path)  # max_episodes_per_show=2
    selected = _select_podcast_episode_files(state_db_path, library_root, profile)

    # fill_mode defaults to "newest" -> highest published_at first, capped at 2.
    assert selected == [podcasts_dir / "ep2.mp3", podcasts_dir / "ep1.mp3"]


# --- plan_rockbox_sync (integration of the pieces above) ---------------------


def test_plan_rockbox_sync_new_device_proposes_adding_everything(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    (library_root / "music" / "Artist" / "Album").mkdir(parents=True)
    track = library_root / "music" / "Artist" / "Album" / "01 Track.m4a"
    track.write_bytes(b"x")

    device_root = tmp_path / "ipod"
    device_root.mkdir()
    state_root = tmp_path / "state"

    from sync_orchestrator import rockbox_sync as rockbox_sync_module

    def fake_resolve_transcode_plan(path, *, options=None):
        from iopenpod.sync.transcoder import TranscodePlan, TranscodeTarget

        return TranscodePlan(
            source_path=Path(path), target=TranscodeTarget.COPY, aac_quality="normal",
            effective_quality="normal", prefer_lossy=False, normalize_sample_rate=False,
            mono_for_spoken=False, smart_quality_by_type=False, video_crf=23,
            video_preset="medium", video_max_width=0, video_max_height=0, video_max_fps=0,
            video_max_bitrate_kbps=0, video_h264_level="3.0",
        )

    monkeypatch.setattr(rockbox_sync_module, "resolve_transcode_plan", fake_resolve_transcode_plan)
    monkeypatch.setattr(
        rockbox_sync_module.BackupManager, "create_backup",
        lambda self, *a, **k: type("Snap", (), {"id": "snap-1"})(),
    )

    profile = _make_profile(tmp_path)
    planned = plan_rockbox_sync(
        device_info=_FakeDeviceInfo(str(device_root)),
        library_root=library_root,
        state_root=state_root,
        profile=profile,
    )

    assert [item.device_relative_path for item in planned.plan.to_add] == ["Music/Artist/Album/01 Track.m4a"]
    assert planned.plan.to_remove == []
    assert planned.plan.bytes_to_add == track.stat().st_size


def test_plan_rockbox_sync_removes_files_no_longer_in_scope(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    (library_root / "music").mkdir(parents=True)
    state_root = tmp_path / "state"

    device_root = tmp_path / "ipod"
    (device_root / "Music" / "Artist").mkdir(parents=True)
    (device_root / "Music" / "Artist" / "Stale.m4a").write_bytes(b"x")

    from sync_orchestrator import rockbox_sync as rockbox_sync_module

    monkeypatch.setattr(
        rockbox_sync_module.BackupManager, "create_backup",
        lambda self, *a, **k: type("Snap", (), {"id": "snap-1"})(),
    )

    profile = _make_profile(tmp_path)
    planned = plan_rockbox_sync(
        device_info=_FakeDeviceInfo(str(device_root)),
        library_root=library_root,
        state_root=state_root,
        profile=profile,
    )

    assert planned.plan.to_remove == ["Music/Artist/Stale.m4a"]


def test_plan_rockbox_sync_external_library_unresolved_selection_reported(monkeypatch, tmp_path):
    library_root = tmp_path / "library"
    (library_root / "music").mkdir(parents=True)
    state_root = tmp_path / "state"
    device_root = tmp_path / "ipod"
    device_root.mkdir()

    external_root = tmp_path / "external"
    external_root.mkdir()

    from sync_orchestrator import rockbox_sync as rockbox_sync_module

    monkeypatch.setattr(
        rockbox_sync_module.BackupManager, "create_backup",
        lambda self, *a, **k: type("Snap", (), {"id": "snap-1"})(),
    )

    profile = _make_profile(tmp_path)
    profile = profile.model_copy(update={
        "external_library": ExternalLibraryConfig(path=str(external_root), selections=["Nonexistent Artist"])
    })

    planned = plan_rockbox_sync(
        device_info=_FakeDeviceInfo(str(device_root)),
        library_root=library_root,
        state_root=state_root,
        profile=profile,
    )

    assert planned.unresolved_selections == ["Nonexistent Artist"]
