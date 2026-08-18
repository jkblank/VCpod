import sqlite3
import struct
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

import iopenpod.device as _iopenpod_device
from iopenpod.artworkdb_writer import artworkdb_chunks as artworkdb_chunks_module
from iopenpod.artworkdb_writer.artwork_types import ArtworkEntry, EncodedFormatPayload
from iopenpod.device.info import DeviceInfo

from iopenpod.sync.transcoder import TranscodeOptions

from sync_orchestrator import sync as sync_module
from sync_orchestrator.sync import (
    SyncError,
    _apply_missing_artwork_index_chunk_workaround,
    _backup_progress_adapter,
    _engine_progress_adapter,
    _MHII_MISSING_INDEX_CHUNK,
    _register_current_device,
    _ThrottledProgressPrinter,
    _transcode_options_for,
    _write_mhii_original,
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


def _make_profile(
    tmp_path: Path, external_library_path: str, transcode_format: str = "alac"
) -> ProfileConfig:
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
            trigger="manual", transcode_format=transcode_format, push_play_status_back=False
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


def test_transcode_options_for_alac_prefers_lossless(tmp_path):
    profile = _make_profile(tmp_path, str(tmp_path), transcode_format="alac")
    assert _transcode_options_for(profile) == TranscodeOptions(prefer_lossy=False)


def test_transcode_options_for_aac_prefers_lossy(tmp_path):
    profile = _make_profile(tmp_path, str(tmp_path), transcode_format="aac")
    assert _transcode_options_for(profile) == TranscodeOptions(prefer_lossy=True)


def test_transcode_options_for_unsupported_format_raises(tmp_path):
    profile = _make_profile(tmp_path, str(tmp_path), transcode_format="mp3")
    with pytest.raises(SyncError, match="transcode_format='mp3'"):
        _transcode_options_for(profile)


def test_register_current_device_returns_real_capabilities_for_known_family():
    # Real DeviceInfo, real (unmocked) capabilities_for_family_gen — this
    # is meant to prove iopenpod's own real table resolves correctly for
    # an already-correctly-identified device (as of iopenpod==1.67.0,
    # enrich() resolves this project's real 5.5th Gen unit natively —
    # see notes.md — so this function no longer needs to hand-correct
    # model_family/generation itself, just register the device and
    # return its capabilities).
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "iPod"
    info.generation = "5.5th Gen"

    capabilities = _register_current_device(info)

    assert capabilities.supports_artwork is True
    assert len(capabilities.cover_art_formats) > 0


def test_register_current_device_registers_device_for_path():
    # Nothing else in our headless path calls iopenpod's own
    # set_current_device() — this registration is still required (not a
    # workaround for a bug, just something our path must do itself).
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "iPod"
    info.generation = "5.5th Gen"

    _register_current_device(info)

    assert _iopenpod_device.get_current_device_for_path("/fake/mount") is info


def test_register_current_device_falls_back_for_unrecognized_family():
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "Some Unknown Device"
    info.generation = ""

    capabilities = _register_current_device(info)

    assert capabilities.supports_artwork is False
    # Identity is left alone — this function no longer tries to correct
    # identity for any family, known or unknown.
    assert info.model_family == "Some Unknown Device"


# --- podcast played-state must reach the add/remove decision -----------------


def _make_episodes_db(tmp_path: Path, rows: list[dict]) -> Path:
    db_path = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE episodes (
            episode_uuid TEXT NOT NULL,
            podcast_uuid TEXT NOT NULL,
            show_name TEXT NOT NULL,
            local_path TEXT NOT NULL,
            played INTEGER NOT NULL,
            played_up_to INTEGER NOT NULL,
            downloaded_at TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            audio_url TEXT NOT NULL DEFAULT '',
            duration_seconds INTEGER NOT NULL DEFAULT 0,
            pending_push INTEGER NOT NULL DEFAULT 0,
            unsubscribed INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (episode_uuid)
        )
        """
    )
    for row in rows:
        conn.execute(
            "INSERT INTO episodes (episode_uuid, podcast_uuid, show_name, local_path, "
            "played, played_up_to, downloaded_at, title, audio_url, duration_seconds) "
            "VALUES (?, ?, ?, ?, ?, 0, '2026-01-01', ?, '', ?)",
            (
                row["guid"],
                row.get("podcast_uuid", "show-1"),
                row.get("show_name", "Show"),
                str(row["local_path"]),
                int(row.get("played", False)),
                row.get("title", ""),
                row.get("duration_seconds", 0),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def test_load_podcast_feeds_sets_listened_override_for_played_episodes(tmp_path):
    # Confirmed live (2026-08-18): episodes marked played in our state db
    # kept getting re-added to the device because this override was never
    # set — see _load_podcast_feeds' docstring for the full trace.
    audio = tmp_path / "played.mp3"
    audio.write_bytes(b"")
    db_path = _make_episodes_db(
        tmp_path,
        [{"guid": "ep-played", "local_path": audio, "played": True, "title": "Played episode"}],
    )

    feeds = sync_module._load_podcast_feeds(str(db_path), tmp_path)

    (episode,) = feeds[0].episodes
    assert episode.listened_override is True


def test_load_podcast_feeds_leaves_unplayed_episodes_untouched(tmp_path):
    # None (not False) is required so device-observed play history can
    # still independently mark the episode listened later — an explicit
    # False is a *sticky* override that blocks that path entirely.
    audio = tmp_path / "unplayed.mp3"
    audio.write_bytes(b"")
    db_path = _make_episodes_db(
        tmp_path,
        [{"guid": "ep-unplayed", "local_path": audio, "played": False, "title": "Unplayed episode"}],
    )

    feeds = sync_module._load_podcast_feeds(str(db_path), tmp_path)

    (episode,) = feeds[0].episodes
    assert episode.listened_override is None


# --- device-reported AlbumArt formats (SysInfoExtended) ----------------------

# Trimmed real excerpt (2026-08-17, real "iPod Classic" 7th Gen/MC293 unit)
# reproducing Apple's actual non-standard shape: a <key> element directly
# inside an <array>, immediately before each item's <dict> — invalid plist,
# confirmed live to make plistlib.loads() raise "unexpected key". Includes
# an unrelated top-level <key>...</key><dict> pair (BuildID-adjacent) to
# prove the sanitizer doesn't touch non-array key/dict pairs.
_REAL_SHAPE_SYSINFO_EXTENDED = b"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
<key>AudioCodecs</key>
<dict>
<key>MP3</key>
<true/>
</dict>
<key>AlbumArt</key>
<array>
<key>1069</key>
<dict>
<key>FormatId</key>
<integer>1069</integer>
<key>RenderWidth</key>
<integer>142</integer>
<key>RenderHeight</key>
<integer>142</integer>
<key>AssociatedFormat</key>
<integer>131072</integer>
<key>ExcludedFormats</key>
<integer>-1</integer>
</dict>
<key>1061</key>
<dict>
<key>FormatId</key>
<integer>1061</integer>
<key>RenderWidth</key>
<integer>55</integer>
<key>RenderHeight</key>
<integer>55</integer>
</dict>
</array>
<key>SerialNumber</key>
<string>8K13762U9ZS</string>
</dict>
</plist>
"""


def _write_sysinfo_extended(mount: Path, content: bytes = _REAL_SHAPE_SYSINFO_EXTENDED) -> None:
    device_dir = mount / "iPod_Control" / "Device"
    device_dir.mkdir(parents=True, exist_ok=True)
    (device_dir / "SysInfoExtended").write_bytes(content)


def test_sanitize_sysinfo_extended_plist_makes_apples_shape_parseable():
    import plistlib

    with pytest.raises(Exception, match="unexpected key"):
        plistlib.loads(_REAL_SHAPE_SYSINFO_EXTENDED)

    sanitized = sync_module._sanitize_sysinfo_extended_plist(_REAL_SHAPE_SYSINFO_EXTENDED)
    plist = plistlib.loads(sanitized)

    # Unrelated top-level key/dict pairs (not inside an <array>) survive
    # untouched — the sanitizer must not be a blanket "drop every <key>
    # before <dict>" pass.
    assert plist["AudioCodecs"] == {"MP3": True}
    assert plist["SerialNumber"] == "8K13762U9ZS"
    assert len(plist["AlbumArt"]) == 2


def test_read_device_album_art_formats_parses_real_device_shape(tmp_path):
    _write_sysinfo_extended(tmp_path)

    formats = sync_module._read_device_album_art_formats(str(tmp_path))

    assert formats == {1069: (142, 142), 1061: (55, 55)}


def test_read_device_album_art_formats_returns_empty_when_file_missing(tmp_path):
    assert sync_module._read_device_album_art_formats(str(tmp_path)) == {}


def test_register_current_device_overrides_artwork_formats_from_real_device(tmp_path):
    # iopenpod's enrich() resolves info.artwork_formats from a static,
    # per-family table (device/capabilities.py's CLASSIC_COVER_ART_FORMATS)
    # that's missing format 1069 and has the wrong dimensions for 1061 —
    # confirmed live against a real "iPod Classic" 7th Gen unit, see the
    # docstring on _read_device_album_art_formats. The device's own
    # SysInfoExtended is authoritative and must win.
    _write_sysinfo_extended(tmp_path)
    info = DeviceInfo(path=str(tmp_path))
    info.model_family = "iPod Classic"
    info.generation = "7th Gen"
    info.artwork_formats = {1055: (128, 128), 1060: (320, 320), 1061: (56, 56), 1068: (128, 128)}

    _register_current_device(info)

    assert info.artwork_formats == {1069: (142, 142), 1061: (55, 55)}


def test_register_current_device_keeps_static_formats_without_sysinfo_extended():
    info = DeviceInfo(path="/fake/mount")
    info.model_family = "iPod Classic"
    info.generation = "7th Gen"
    info.artwork_formats = {1055: (128, 128)}

    _register_current_device(info)

    assert info.artwork_formats == {1055: (128, 128)}


# --- missing ArtworkDB index chunk workaround --------------------------------


def _make_artwork_entry() -> ArtworkEntry:
    payload = EncodedFormatPayload(
        data=b"\x00" * 20000, width=100, height=100, size=20000, stride_pixels=100
    )
    return ArtworkEntry(
        img_id=1, db_track_id=2, art_hash=None, src_img_size=20000, formats={1028: payload}
    )


def test_write_mhii_with_missing_index_chunk_matches_real_itunes_shape():
    # Real iTunes writes a third mhii child (an mhod type 6 wrapping a
    # fixed all-zero mhaf sub-chunk) that iopenpod's own _write_mhii()
    # never emits — confirmed live by byte-diffing a real-iTunes-written
    # ArtworkDB against one this project wrote for the same device: every
    # one of 1141/1141 real entries has it, 0/5555 of iopenpod's do. See
    # notes.md and the _MHII_MISSING_INDEX_CHUNK docstring in sync.py.
    entry = _make_artwork_entry()
    format_locations = {1028: 0}

    original_bytes = _write_mhii_original(entry, format_locations)
    patched_bytes = sync_module._write_mhii_with_missing_index_chunk(entry, format_locations)

    # Header (magic + header_size) and every child byte are unchanged;
    # only total_len/child_count (both inside the header) are bumped, and
    # the missing chunk is appended after the original children.
    assert patched_bytes[:8] == original_bytes[:8]
    assert patched_bytes[16:len(original_bytes)] == original_bytes[16:]
    assert patched_bytes[len(original_bytes):] == _MHII_MISSING_INDEX_CHUNK

    orig_total_len, orig_child_count = struct.unpack_from("<II", original_bytes, 8)
    new_total_len, new_child_count = struct.unpack_from("<II", patched_bytes, 8)
    assert new_total_len == orig_total_len + len(_MHII_MISSING_INDEX_CHUNK)
    assert new_child_count == orig_child_count + 1


def test_apply_missing_artwork_index_chunk_workaround_patches_module(monkeypatch):
    monkeypatch.setattr(artworkdb_chunks_module, "_write_mhii", _write_mhii_original)

    _apply_missing_artwork_index_chunk_workaround()

    assert artworkdb_chunks_module._write_mhii is sync_module._write_mhii_with_missing_index_chunk


def test_apply_missing_artwork_index_chunk_workaround_is_idempotent(monkeypatch):
    monkeypatch.setattr(artworkdb_chunks_module, "_write_mhii", _write_mhii_original)
    entry = _make_artwork_entry()
    format_locations = {1028: 0}

    _apply_missing_artwork_index_chunk_workaround()
    _apply_missing_artwork_index_chunk_workaround()

    # Calling the setup twice must not double-append the chunk — each
    # wrapped call always delegates to the untouched original captured at
    # import time, not whatever the module attribute currently points at.
    result = artworkdb_chunks_module._write_mhii(entry, format_locations)
    expected = sync_module._write_mhii_with_missing_index_chunk(entry, format_locations)
    assert result == expected
    assert result.count(_MHII_MISSING_INDEX_CHUNK) == 1
