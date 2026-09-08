"""JSON-safe summaries of a computed/executed sync -- the structured
counterpart to cli.py's _print_plan/_run_sync text output, built off
the exact same fields (iopenpod's SyncPlan/SyncItem/StorageSummary, see
.venv/lib/.../iopenpod/sync/contracts.py), so the two representations
never drift. Used by `sync-orchestrator sync --json` and, downstream,
by web-gui-backend's streaming sync routes -- sync-orchestrator stays a
standalone project so this is the only channel a GUI has into a real
sync's outcome (see device.py's own docstring for why it's kept
isolated from any root-workspace service)."""

from __future__ import annotations

from collections import Counter
from typing import Any

# Same caps _print_plan already uses -- a huge library must never dump
# thousands of track labels over the wire (or the terminal).
_SAMPLE_CAP_SMALL = 10
_SAMPLE_CAP_LARGE = 20


def _label_sample(items: list, cap: int) -> tuple[list[str], int]:
    labels = [item.display_label for item in items[:cap]]
    return labels, max(0, len(items) - cap)


def _playlist_titles(playlists: list[dict]) -> list[str]:
    return [p.get("Title") or p.get("title") or p.get("name") or str(p) for p in playlists]


def plan_summary(planned: Any) -> dict[str, Any]:
    """planned: sync.PlannedSync. Everything here is read the same way
    cli.py's _print_plan/_run_sync already reads it -- no new fields on
    the underlying plan object, just structured instead of printed."""
    plan = planned.plan

    to_add_sample, to_add_more = _label_sample(plan.to_add, _SAMPLE_CAP_SMALL)
    to_remove_sample, to_remove_more = _label_sample(plan.to_remove, _SAMPLE_CAP_LARGE)

    field_counts: Counter[str] = Counter()
    for item in plan.to_update_metadata:
        field_counts.update(item.metadata_changes.keys())

    return {
        "to_add_count": len(plan.to_add),
        "to_remove_count": len(plan.to_remove),
        "to_update_metadata_count": len(plan.to_update_metadata),
        "to_update_file_count": len(plan.to_update_file),
        "to_update_artwork_count": len(plan.to_update_artwork),
        "to_add_sample": to_add_sample,
        "to_add_sample_more": to_add_more,
        "to_remove_sample": to_remove_sample,
        "to_remove_sample_more": to_remove_more,
        "metadata_field_changes": dict(field_counts.most_common(20)),
        "duplicates_count": len(plan.duplicates),
        "playlists_to_add": _playlist_titles(plan.playlists_to_add),
        "playlists_to_edit": _playlist_titles(plan.playlists_to_edit),
        "playlists_to_remove": _playlist_titles(plan.playlists_to_remove),
        "storage": {
            "bytes_to_add": plan.storage.bytes_to_add,
            "bytes_to_remove": plan.storage.bytes_to_remove,
            "bytes_to_update": plan.storage.bytes_to_update,
            "net_change": plan.storage.net_change,
        },
        "unresolved_selections": planned.unresolved_selections,
        "unresolved_audiobook_selections": planned.unresolved_audiobook_selections,
        "unresolved_music_selections": planned.unresolved_music_selections,
        "play_states_updated": planned.play_states_updated,
        "before_track_count": planned.before_track_count,
    }


def result_summary(*, exec_result: Any, after: dict, planned: Any, ejected: bool) -> dict[str, Any]:
    """exec_result/after: execute_sync()'s own return values. Mirrors
    the fields _run_sync already prints after a successful --execute."""
    return {
        "summary": exec_result.summary,
        "tracks_added": exec_result.tracks_added,
        "after_track_count": len(after.get("mhlt", [])),
        "before_track_count": planned.before_track_count,
        "snapshot_id": planned.snapshot.id if planned.snapshot is not None else None,
        "ejected": ejected,
    }
