import shutil
from pathlib import Path

from common.playlist import write_m3u8
from common.state import StateDB, TrackRecord

from library_manager.dedup import choose_canonical, find_duplicate_groups, quarantine_duplicates
from library_manager.scan import TrackInfo

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _track(source: str, source_id: str, title: str, artist: str, isrc: str | None = None) -> TrackInfo:
    return TrackInfo(
        path=Path(f"/library/music/{artist}/{title}.m4a"),
        source=source,
        source_id=source_id,
        title=title,
        artist=artist,
        isrc=isrc,
    )


def test_find_duplicate_groups_by_isrc_across_sources():
    a = _track("apple_music", "a1", "Song", "Artist", isrc="US0001")
    b = _track("spotify", "s1", "Song", "Artist", isrc="US0001")
    groups = find_duplicate_groups([a, b])
    assert groups == [[a, b]]


def test_find_duplicate_groups_same_isrc_same_source_not_grouped():
    a = _track("apple_music", "a1", "Song", "Artist", isrc="US0001")
    b = _track("apple_music", "a2", "Song (Remaster)", "Artist", isrc="US0001")
    assert find_duplicate_groups([a, b]) == []


def test_find_duplicate_groups_fuzzy_fallback_above_threshold():
    a = _track("apple_music", "a1", "Hey Jude", "The Beatles")
    b = _track("spotify", "s1", "hey   jude", "the beatles")
    groups = find_duplicate_groups([a, b])
    assert groups == [[a, b]]


def test_find_duplicate_groups_fuzzy_below_threshold_not_grouped():
    a = _track("apple_music", "a1", "Hey Jude", "The Beatles")
    b = _track("spotify", "s1", "Let It Be", "The Beatles")
    assert find_duplicate_groups([a, b]) == []


def test_find_duplicate_groups_same_source_never_grouped_by_fuzzy():
    a = _track("apple_music", "a1", "Hey Jude", "The Beatles")
    b = _track("apple_music", "a2", "hey jude", "the beatles")
    assert find_duplicate_groups([a, b]) == []


def test_choose_canonical_respects_fidelity_order():
    apple = _track("apple_music", "a1", "Song", "Artist")
    spotify = _track("spotify", "s1", "Song", "Artist")
    ytmusic = _track("ytmusic", "y1", "Song", "Artist")
    assert choose_canonical([spotify, ytmusic, apple]) is apple
    assert choose_canonical([spotify, ytmusic]) is spotify


def test_choose_canonical_unknown_source_sorts_last():
    apple = _track("apple_music", "a1", "Song", "Artist")
    mystery = _track("some_future_source", "m1", "Song", "Artist")
    assert choose_canonical([mystery, apple]) is apple


def test_quarantine_duplicates_end_to_end(tmp_path: Path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    state_dir = tmp_path / "state"

    apple_dir = library_root / "Fixture Artist" / "Fixture Album"
    apple_dir.mkdir(parents=True)
    apple_path = apple_dir / "apple_track.m4a"
    shutil.copy(FIXTURES / "tagged.m4a", apple_path)

    spotify_dir = library_root / "Fixture Artist" / "Other Album"
    spotify_dir.mkdir(parents=True)
    spotify_path = spotify_dir / "spotify_track.mp3"
    shutil.copy(FIXTURES / "tagged.mp3", spotify_path)

    # tagged.m4a and tagged.mp3 share isrc "USTEST0000001" by construction.
    from library_manager.scan import scan_library

    tracks = scan_library(library_root)
    groups = find_duplicate_groups(tracks)
    assert len(groups) == 1

    state_dir.mkdir()
    john_db = state_dir / "john.sqlite"
    with StateDB(john_db) as db:
        db.record_track(
            TrackRecord(
                source="apple_music",
                source_id="111111111",
                local_path=str(apple_path),
                title="Fixture Title",
                artist="Fixture Artist",
                downloaded_at="2026-01-01T00:00:00+00:00",
            )
        )
        db.record_track(
            TrackRecord(
                source="spotify",
                source_id="222222222",
                local_path=str(spotify_path),
                title="Fixture Title",
                artist="Fixture Artist",
                downloaded_at="2026-01-01T00:00:00+00:00",
            )
        )

    apple_playlist = playlists_root / "john" / "Apple Playlist.m3u8"
    write_m3u8(apple_playlist, [apple_path])
    spotify_playlist = playlists_root / "john" / "Spotify Playlist.m3u8"
    write_m3u8(spotify_playlist, [spotify_path])

    result = quarantine_duplicates(
        groups,
        library_root=library_root,
        playlists_root=playlists_root,
        state_db_paths=[john_db],
    )

    assert len(result.canonical) == 1
    assert result.canonical[0].source == "apple_music"
    assert apple_path.exists()  # canonical file stays put
    assert not spotify_path.exists()  # duplicate moved away

    quarantined_track, quarantined_path = result.quarantined[0]
    assert quarantined_track.source == "spotify"
    assert quarantined_path == library_root / ".duplicates" / "spotify" / "222222222.mp3"
    assert quarantined_path.exists()

    with StateDB(john_db) as db:
        assert db.get_track("apple_music", "111111111").local_path == str(apple_path)
        assert db.get_track("spotify", "222222222").local_path == str(apple_path)

    # apple playlist already pointed at the canonical file — untouched.
    assert apple_playlist.read_text().splitlines()[1:] == [str(apple_path)]
    # spotify playlist gets rewritten to point at the canonical file instead.
    assert spotify_playlist.read_text().splitlines()[1:] == [str(apple_path)]


def test_quarantine_duplicates_no_groups_is_a_no_op(tmp_path: Path):
    library_root = tmp_path / "library"
    playlists_root = tmp_path / "playlists"
    library_root.mkdir()
    playlists_root.mkdir()

    result = quarantine_duplicates(
        [], library_root=library_root, playlists_root=playlists_root, state_db_paths=[]
    )

    assert result.canonical == []
    assert result.quarantined == []
