import shutil
from pathlib import Path

from fetcher_spotify.tag import add_dedup_tags, add_isrc_tag, read_dedup_tags, read_isrc_tag

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_add_and_read_dedup_tags_round_trip(tmp_path: Path):
    target = tmp_path / "track.mp3"
    shutil.copy(FIXTURES / "track1.mp3", target)

    assert read_dedup_tags(target) == (None, None)

    add_dedup_tags(target, "spotify", "07nXuVDzsdtzjglNc8F1UI")

    assert read_dedup_tags(target) == ("spotify", "07nXuVDzsdtzjglNc8F1UI")


def test_add_and_read_isrc_tag_round_trip(tmp_path: Path):
    target = tmp_path / "track.mp3"
    shutil.copy(FIXTURES / "track1.mp3", target)

    assert read_isrc_tag(target) is None

    add_isrc_tag(target, "USRC12345678")

    assert read_isrc_tag(target) == "USRC12345678"
