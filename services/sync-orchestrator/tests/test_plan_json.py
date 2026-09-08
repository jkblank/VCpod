from __future__ import annotations

from types import SimpleNamespace

from sync_orchestrator.plan_json import plan_summary, result_summary


def _item(label: str, metadata_changes: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(display_label=label, metadata_changes=metadata_changes or {})


def _plan(**overrides) -> SimpleNamespace:
    defaults = dict(
        to_add=[],
        to_remove=[],
        to_update_metadata=[],
        to_update_file=[],
        to_update_artwork=[],
        duplicates={},
        playlists_to_add=[],
        playlists_to_edit=[],
        playlists_to_remove=[],
        storage=SimpleNamespace(bytes_to_add=0, bytes_to_remove=0, bytes_to_update=0, net_change=0),
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _planned(**overrides) -> SimpleNamespace:
    defaults = dict(
        plan=_plan(),
        before_track_count=0,
        snapshot=None,
        unresolved_selections=[],
        unresolved_audiobook_selections=[],
        unresolved_music_selections=[],
        play_states_updated=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_plan_summary_counts_and_samples_to_add():
    items = [_item(f"track {i}") for i in range(15)]
    planned = _planned(plan=_plan(to_add=items))

    summary = plan_summary(planned)

    assert summary["to_add_count"] == 15
    # Same 10-item cap _print_plan already uses for to_add.
    assert summary["to_add_sample"] == [f"track {i}" for i in range(10)]
    assert summary["to_add_sample_more"] == 5


def test_plan_summary_caps_to_remove_at_twenty():
    items = [_item(f"track {i}") for i in range(25)]
    planned = _planned(plan=_plan(to_remove=items))

    summary = plan_summary(planned)

    assert summary["to_remove_count"] == 25
    assert len(summary["to_remove_sample"]) == 20
    assert summary["to_remove_sample_more"] == 5


def test_plan_summary_no_overflow_marker_when_under_cap():
    planned = _planned(plan=_plan(to_add=[_item("only one")]))

    summary = plan_summary(planned)

    assert summary["to_add_sample_more"] == 0


def test_plan_summary_playlist_titles_use_fallback_chain():
    # Real iopenpod playlist dicts key the name as 'Title' (capitalized) --
    # same fallback chain _print_plan already uses (Title/title/name/raw).
    planned = _planned(
        plan=_plan(
            playlists_to_add=[{"Title": "Workout"}],
            playlists_to_edit=[{"title": "lowercase"}],
            playlists_to_remove=[{"name": "fallback name"}],
        )
    )

    summary = plan_summary(planned)

    assert summary["playlists_to_add"] == ["Workout"]
    assert summary["playlists_to_edit"] == ["lowercase"]
    assert summary["playlists_to_remove"] == ["fallback name"]


def test_plan_summary_counts_metadata_field_changes():
    items = [
        _item("a", metadata_changes={"title": "x", "artist": "y"}),
        _item("b", metadata_changes={"title": "z"}),
    ]
    planned = _planned(plan=_plan(to_update_metadata=items))

    summary = plan_summary(planned)

    assert summary["to_update_metadata_count"] == 2
    assert summary["metadata_field_changes"] == {"title": 2, "artist": 1}


def test_plan_summary_includes_storage_and_unresolved_selections():
    planned = _planned(
        plan=_plan(
            storage=SimpleNamespace(
                bytes_to_add=1000, bytes_to_remove=200, bytes_to_update=50, net_change=850
            ),
            duplicates={"key": [1, 2]},
        ),
        unresolved_selections=["Typo Artist"],
        unresolved_audiobook_selections=["Typo Author"],
        unresolved_music_selections=["Typo Band"],
        play_states_updated=3,
        before_track_count=42,
    )

    summary = plan_summary(planned)

    assert summary["storage"] == {
        "bytes_to_add": 1000,
        "bytes_to_remove": 200,
        "bytes_to_update": 50,
        "net_change": 850,
    }
    assert summary["duplicates_count"] == 1
    assert summary["unresolved_selections"] == ["Typo Artist"]
    assert summary["unresolved_audiobook_selections"] == ["Typo Author"]
    assert summary["unresolved_music_selections"] == ["Typo Band"]
    assert summary["play_states_updated"] == 3
    assert summary["before_track_count"] == 42


def test_result_summary_with_snapshot():
    planned = _planned(before_track_count=10, snapshot=SimpleNamespace(id="snap-42"))
    exec_result = SimpleNamespace(summary="wrote 3 tracks", tracks_added=3)

    result = result_summary(
        exec_result=exec_result, after={"mhlt": [1, 2, 3, 4]}, planned=planned, ejected=True
    )

    assert result == {
        "summary": "wrote 3 tracks",
        "tracks_added": 3,
        "after_track_count": 4,
        "before_track_count": 10,
        "snapshot_id": "snap-42",
        "ejected": True,
    }


def test_result_summary_without_snapshot():
    planned = _planned(snapshot=None)
    exec_result = SimpleNamespace(summary="ok", tracks_added=0)

    result = result_summary(exec_result=exec_result, after={"mhlt": []}, planned=planned, ejected=False)

    assert result["snapshot_id"] is None
