import io
import shutil
from pathlib import Path

from mutagen.id3 import ID3, APIC
from mutagen.mp4 import MP4, MP4Cover
from PIL import Image

from library_manager.artwork import normalize_library_artwork, normalize_track_artwork

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _clean_jpeg(size: tuple[int, int] = (64, 64)) -> bytes:
    img = Image.new("RGB", size, color=(120, 30, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _jpeg_with_dri_marker(size: tuple[int, int] = (64, 64)) -> bytes:
    """A baseline JPEG with a DRI (Define Restart Interval) marker
    spliced in right before the start-of-scan marker — the same
    signature found on the real "Cool Kids" track that broke on-device
    album art rendering. Pillow's own encoder never writes this."""
    data = _clean_jpeg(size)
    sos_index = data.find(b"\xff\xda")
    assert sos_index != -1
    dri_segment = b"\xff\xdd\x00\x04\x00\x08"  # DRI, length=4, interval=8
    return data[:sos_index] + dri_segment + data[sos_index:]


def _m4a_with_art(tmp_path: Path, art: bytes) -> Path:
    dest = tmp_path / "track.m4a"
    shutil.copyfile(FIXTURES / "tagged.m4a", dest)
    mp4 = MP4(dest)
    mp4.tags["covr"] = [MP4Cover(art, imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()
    return dest


def _mp3_with_art(tmp_path: Path, art: bytes) -> Path:
    dest = tmp_path / "track.mp3"
    shutil.copyfile(FIXTURES / "tagged.mp3", dest)
    id3 = ID3(dest)
    id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=art))
    id3.save(dest, v2_version=3)
    return dest


def test_normalize_mp4_reencodes_track_with_dri_marker(tmp_path: Path):
    path = _m4a_with_art(tmp_path, _jpeg_with_dri_marker())

    changed = normalize_track_artwork(path)

    assert changed is True
    covr = MP4(path).tags.get("covr")[0]
    assert b"\xff\xdd" not in bytes(covr)
    img = Image.open(io.BytesIO(bytes(covr)))
    img.load()
    assert img.size == (64, 64)  # dimensions preserved -- this is a re-encode, not a resize


def test_normalize_mp4_leaves_clean_track_untouched(tmp_path: Path):
    path = _m4a_with_art(tmp_path, _clean_jpeg())
    original_bytes = bytes(MP4(path).tags.get("covr")[0])

    changed = normalize_track_artwork(path)

    assert changed is False
    assert bytes(MP4(path).tags.get("covr")[0]) == original_bytes


def test_normalize_mp4_no_embedded_art_is_a_noop(tmp_path: Path):
    dest = tmp_path / "track.m4a"
    shutil.copyfile(FIXTURES / "untagged.m4a", dest)

    assert normalize_track_artwork(dest) is False


def test_normalize_is_idempotent_on_repeat_runs(tmp_path: Path):
    path = _m4a_with_art(tmp_path, _jpeg_with_dri_marker())

    assert normalize_track_artwork(path) is True
    assert normalize_track_artwork(path) is False  # already clean -- no second re-encode


def test_normalize_id3_reencodes_track_with_dri_marker(tmp_path: Path):
    path = _mp3_with_art(tmp_path, _jpeg_with_dri_marker())

    changed = normalize_track_artwork(path)

    assert changed is True
    apic = ID3(path).getall("APIC")[0]
    assert b"\xff\xdd" not in apic.data


def test_normalize_id3_leaves_clean_track_untouched(tmp_path: Path):
    path = _mp3_with_art(tmp_path, _clean_jpeg())
    original_data = ID3(path).getall("APIC")[0].data

    assert normalize_track_artwork(path) is False
    assert ID3(path).getall("APIC")[0].data == original_data


def test_normalize_track_artwork_dry_run_reports_without_writing(tmp_path: Path):
    path = _m4a_with_art(tmp_path, _jpeg_with_dri_marker())
    original_bytes = bytes(MP4(path).tags.get("covr")[0])

    would_change = normalize_track_artwork(path, dry_run=True)

    assert would_change is True
    assert bytes(MP4(path).tags.get("covr")[0]) == original_bytes  # untouched


def test_normalize_library_artwork_scans_and_reports(tmp_path: Path):
    library_root = tmp_path / "music"
    (library_root / "Artist A").mkdir(parents=True)
    (library_root / "Artist B").mkdir(parents=True)
    affected = _m4a_with_art(library_root / "Artist A", _jpeg_with_dri_marker())
    clean = _m4a_with_art(library_root / "Artist B", _clean_jpeg())

    result = normalize_library_artwork(library_root)

    assert result.scanned == 2
    assert result.normalized == [affected]
    assert result.failures == []
    assert clean not in result.normalized


def test_normalize_library_artwork_reports_undecodable_art_as_failure(tmp_path: Path):
    library_root = tmp_path / "music"
    library_root.mkdir()
    dest = library_root / "track.m4a"
    shutil.copyfile(FIXTURES / "tagged.m4a", dest)
    mp4 = MP4(dest)
    # A marker sequence that trips _jpeg_needs_reencode's DRI check but
    # isn't a real decodable JPEG -- Image.open()/load() must raise, and
    # that failure must be caught and reported, not propagated.
    corrupt = b"\xff\xd8\xff\xdd\x00\x04\x00\x08" + b"\x00" * 32
    mp4.tags["covr"] = [MP4Cover(corrupt, imageformat=MP4Cover.FORMAT_JPEG)]
    mp4.save()

    result = normalize_library_artwork(library_root)

    assert result.scanned == 1
    assert result.normalized == []
    assert len(result.failures) == 1
    assert result.failures[0][0] == dest
