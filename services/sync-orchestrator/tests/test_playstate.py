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


def test_missing_db_track_id_is_skipped():
    before = {"mhlt": [{"recent_playcount": 1, "bookmark_time": 100_000}]}
    mapping = _FakeMappingFile({1: "/library/podcasts/Show/ep.mp3"})

    result = resolve_played_states(before, mapping, {"/library/podcasts/Show/ep.mp3": 1800})

    assert result == {}
