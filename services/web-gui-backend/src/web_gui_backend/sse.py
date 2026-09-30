"""Server-Sent Events framing shared by every route that streams a
long-running operation's progress to the browser (routers/sync.py,
routers/audiobooks_discover.py) -- pulled out here once a second real
caller needed the exact same framing logic as the first (same reasoning
common.config.resolve_config_path was moved out of music-stack-cli for)."""

from __future__ import annotations


def sse_event(event: str, data: str) -> str:
    # Multi-line-safe SSE framing: one "data: " line per line of data,
    # per the SSE spec (a browser EventSource/manual parser concatenates
    # consecutive data: lines with '\n' when reconstructing). Every event
    # emitted by callers today happens to be single-line (a progress log
    # line, or a single-line json.dumps(...) result) but this stays
    # correct if that ever changes.
    lines = data.splitlines() or [""]
    data_block = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{data_block}\n\n"
