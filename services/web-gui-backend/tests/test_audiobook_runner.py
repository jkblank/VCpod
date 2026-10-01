from __future__ import annotations

import json

import pytest

from audiobook_manager.pipeline import ImportOutcome, ImportPipelineError
from web_gui_backend import audiobook_runner
from web_gui_backend.audiobook_runner import stream_import_audiobook

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_stream_import_audiobook_relays_progress_then_result(monkeypatch, tmp_path):
    captured = {}

    def fake_run_import_audiobook(
        parts_dir, *, library_root, state_root, progress_callback=None
    ):
        captured["parts_dir"] = parts_dir
        captured["library_root"] = library_root
        captured["state_root"] = state_root
        # Simulate run_import_audiobook's real behavior: progress_callback
        # is invoked synchronously, from this (background-thread) call,
        # as the real merge/tag pipeline would -- confirms those calls
        # genuinely cross the background-thread/event-loop boundary and
        # arrive as ("progress", ...) events in order, not just that the
        # plumbing type-checks.
        progress_callback("probing 1 part(s)...")
        progress_callback("merging 1 part(s) via ffmpeg (codec=aac, 64k)...")
        return ImportOutcome(
            imported=True,
            imported_paths=[tmp_path / "library" / "Some Author" / "Some Book.m4b"],
            staging_dir=tmp_path / "state" / "audiobooks" / "staging" / "Some Book",
        )

    monkeypatch.setattr(audiobook_runner, "run_import_audiobook", fake_run_import_audiobook)

    events = []
    async for event, data in stream_import_audiobook(
        tmp_path / "Some Book", library_root=tmp_path / "library", state_root=tmp_path / "state"
    ):
        events.append((event, data))

    kinds = [k for k, _ in events]
    assert kinds == ["progress", "progress", "result"]
    assert events[0] == ("progress", "probing 1 part(s)...")
    assert events[1] == ("progress", "merging 1 part(s) via ffmpeg (codec=aac, 64k)...")
    result = json.loads(events[2][1])
    assert result["imported"] is True
    assert result["imported_paths"] == [
        str(tmp_path / "library" / "Some Author" / "Some Book.m4b")
    ]
    assert captured["parts_dir"] == tmp_path / "Some Book"


async def test_stream_import_audiobook_yields_error_on_pipeline_failure(monkeypatch, tmp_path):
    def fake_run_import_audiobook(
        parts_dir, *, library_root, state_root, progress_callback=None
    ):
        progress_callback("starting import for 'Some Book'")
        raise ImportPipelineError("ffmpeg not found on PATH")

    monkeypatch.setattr(audiobook_runner, "run_import_audiobook", fake_run_import_audiobook)

    events = []
    async for event, data in stream_import_audiobook(
        tmp_path / "Some Book", library_root=tmp_path / "library", state_root=tmp_path / "state"
    ):
        events.append((event, data))

    assert events == [
        ("progress", "starting import for 'Some Book'"),
        ("error", "ffmpeg not found on PATH"),
    ]
