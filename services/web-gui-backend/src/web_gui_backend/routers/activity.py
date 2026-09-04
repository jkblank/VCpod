"""GET /api/activity -- the cross-profile job-history feed backing the
Activity screen, reading common.activity's shared state/activity.sqlite
log (written to by fetch-scheduler and sync-orchestrator)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from common.activity import list_activity

router = APIRouter()


@router.get("/api/activity")
def activity(request: Request, limit: int = 50) -> dict:
    entries = list_activity(request.app.state.state_root, limit=limit)
    return {
        "entries": [
            {
                "started_at": entry.started_at.isoformat(),
                "service": entry.service,
                "profile": entry.profile,
                "description": entry.description,
                "duration_seconds": entry.duration_seconds,
                "result": entry.result,
            }
            for entry in entries
        ]
    }
