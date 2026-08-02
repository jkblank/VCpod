from sync_orchestrator.playstate import resolve_played_states


class _FakeTrackMapping:
    def __init__(self, source_path_hint):
        self.source_path_hint = source_path_hint


class _FakeMappingFile:
    def __init__(self, by_db_track_id: dict[int, str]):
        self._by_db_track_id = by_db_track_id

    def get_by_db_track_id(self, db_track_id: int):
        path = self._by_db_track_id.get(db_track_id)
        if path is None:
            return None
        return ("fake-fingerprint", _FakeTrackMapping(path))


def test_track_with_no_delta_is_skipped():
    before = {"mhlt": [{"db_track_id": 1, "recent_playcount": 0, "bookmark_time": 0}]}
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {}


def test_full_play_with_known_duration_marks_played():
    before = {
        "mhlt": [{"db_track_id": 1, "recent_playcount": 1, "bookmark_time": 1_700_000}]
    }
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {"/library/podcasts/Show/ep.mp3": (True, 1700)}


def test_partial_play_with_known_duration_is_in_progress_not_played():
    before = {
        "mhlt": [{"db_track_id": 1, "recent_playcount": 1, "bookmark_time": 300_000}]
    }
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {"/library/podcasts/Show/ep.mp3": (False, 300)}


def test_bookmark_moved_without_playcount_is_in_progress():
    # A seek/resume with no completed play registered this session yet.
    before = {"mhlt": [{"db_track_id": 1, "recent_playcount": 0, "bookmark_time": 5_000}]}
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {"/library/podcasts/Show/ep.mp3": (False, 5)}


def test_playcount_without_known_duration_falls_back_to_played():
    before = {
        "mhlt": [{"db_track_id": 1, "recent_playcount": 1, "bookmark_time": 100_000}]
    }
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 0})

    assert result == {"/library/podcasts/Show/ep.mp3": (True, 100)}


def test_track_not_in_mapping_is_skipped():
    before = {"mhlt": [{"db_track_id": 999, "recent_playcount": 1, "bookmark_time": 100_000}]}
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {}


def test_track_not_a_known_episode_path_is_skipped():
    # e.g. a music track: resolves fine via the mapping, but its path
    # isn't in durations_by_path (only podcast episodes are), so it's
    # silently not treated as podcast state.
    before = {"mhlt": [{"db_track_id": 1, "recent_playcount": 1, "bookmark_time": 100_000}]}
    mapping = _FakeMappingFile({1: "/library/music/Artist/Album/track.m4a"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {}


def test_near_end_position_marks_played_even_with_zero_playcount():
    # A click-wheel iPod's own play-count only increments on a natural
    # track completion, not on pressing skip/next — a real user pressed
    # skip one minute before the end of an hour-long (3600s) episode,
    # landing at 3540s (98.3%), and recent_playcount stayed 0. This must
    # still count as played, since position alone is trusted once past
    # PLAYED_THRESHOLD when duration is known.
    before = {
        "mhlt": [{"db_track_id": 1, "recent_playcount": 0, "bookmark_time": 3_540_000}]
    }
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 3600})

    assert result == {"/library/podcasts/Show/ep.mp3": (True, 3540)}


def test_bare_filename_source_path_hint_matches_full_local_path():
    # Regression: iopenpod's own mapping file (iOpenPod.json) stores
    # TrackMapping.source_path_hint as a bare filename, not the absolute
    # path our state db's local_path uses -- confirmed live against a
    # real device, where every podcast episode with real activity failed
    # the old exact-path membership check, 8/8. Matching by filename
    # alone is safe because our episode filenames embed the Pocket Casts
    # episode UUID (globally unique) -- see download.py's _episode_path.
    before = {
        "mhlt": [{"db_track_id": 1, "recent_playcount": 1, "bookmark_time": 1_700_000}]
    }
    mapping = _FakeMappingFile({1: "Episode Title [abc-123].mp3"})
    local_path = "/home/john/Music/music-stack/library/podcasts/Show/Episode Title [abc-123].mp3"

    result = resolve_played_states(before, mapping, {local_path: 1800})

    assert result == {local_path: (True, 1700)}


def test_missing_db_track_id_is_skipped():
    before = {"mhlt": [{"recent_playcount": 1, "bookmark_time": 100_000}]}
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {}
