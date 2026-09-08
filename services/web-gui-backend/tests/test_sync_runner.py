from __future__ import annotations

import asyncio

import pytest

from web_gui_backend.sync_runner import stream_sync

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _write_profile(path, *, match_value="NO-SUCH-SERIAL") -> None:
    path.write_text(
        f"""
profile: test
device:
  match_by: serial
  match_value: "{match_value}"
playlists: []
podcasts:
  pocketcasts:
    credentials_file: /config/secrets/pocketcasts/test.json
  sync_unplayed_only: true
  max_episodes_per_show: 5
  shows: all
sync:
  trigger: manual
  transcode_format: alac
  push_play_status_back: false
"""
    )


async def test_stream_sync_reports_device_not_found_as_error_event(tmp_path):
    # Fully real: a real sync-orchestrator subprocess, real --json flag,
    # a profile that can never match a real connected device -- exercises
    # the whole asyncio subprocess/stream-reading path end to end without
    # needing a real iPod plugged in.
    profile_path = tmp_path / "profile.yaml"
    _write_profile(profile_path)

    events = []
    async for event in stream_sync(
        args=[
            "--profile", str(profile_path),
            "--library-root", str(tmp_path / "library"),
            "--state-root", str(tmp_path / "state"),
        ],
        sync_orchestrator_dir="services/sync-orchestrator",
    ):
        events.append(event)

    kinds = [kind for kind, _ in events]
    assert kinds[-1] == "error"
    error_message = events[-1][1]
    assert "no connected, mounted iPod matches" in error_message
    # At least the "Finding device" progress line should have arrived
    # before the terminal error event -- proves progress really streams
    # rather than only showing up after the process exits.
    assert any(kind == "progress" for kind in kinds[:-1])


async def test_stream_sync_reports_bad_project_dir_as_error_event():
    # `uv` itself is real and on PATH here, so this doesn't hit the
    # can't-even-spawn OSError branch (that's covered separately below
    # with a mocked create_subprocess_exec) -- it's `uv run --project`
    # itself failing loudly (nonzero exit) against a nonexistent
    # project dir, which the terminal event must still surface as an
    # error rather than silently swallowing.
    events = []
    async for event in stream_sync(
        args=["--profile", "x", "--library-root", "y", "--state-root", "z"],
        sync_orchestrator_dir="/no/such/project/dir/at/all",
    ):
        events.append(event)

    assert events[-1][0] == "error"


async def test_stream_sync_reports_os_error_starting_process_as_error_event(monkeypatch):
    async def _raise_os_error(*args, **kwargs):
        raise OSError("uv not found")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _raise_os_error)

    events = [
        e
        async for e in stream_sync(
            args=["--profile", "x"], sync_orchestrator_dir="/whatever"
        )
    ]

    assert len(events) == 1
    assert events[0][0] == "error"
    assert "could not start sync-orchestrator" in events[0][1]


class _FakeStream:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


class _FakeProcess:
    def __init__(self, *, stdout_lines: list[bytes], stderr_lines: list[bytes], returncode: int) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(stderr_lines)
        self._returncode = returncode

    async def wait(self) -> int:
        return self._returncode


async def test_stream_sync_yields_progress_then_result_for_a_successful_run(monkeypatch):
    fake = _FakeProcess(
        stdout_lines=[b'{"to_add_count": 3}\n'],
        stderr_lines=[b"  == Finding device ==\n", b"  scanning...\n"],
        returncode=0,
    )

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    events = []
    async for event in stream_sync(args=["--profile", "x"], sync_orchestrator_dir="/whatever"):
        events.append(event)

    assert events == [
        ("progress", "  == Finding device =="),
        ("progress", "  scanning..."),
        ("result", '{"to_add_count": 3}'),
    ]


async def test_stream_sync_reports_unparseable_stdout_as_error(monkeypatch):
    fake = _FakeProcess(stdout_lines=[b"not json\n"], stderr_lines=[], returncode=0)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    events = [e async for e in stream_sync(args=[], sync_orchestrator_dir="/whatever")]

    assert events[-1][0] == "error"
    assert "could not parse" in events[-1][1]


async def test_stream_sync_nonzero_exit_uses_stdout_as_error_message(monkeypatch):
    fake = _FakeProcess(stdout_lines=[b"FAIL: something broke\n"], stderr_lines=[], returncode=1)

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return fake

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    events = [e async for e in stream_sync(args=[], sync_orchestrator_dir="/whatever")]

    assert events == [("error", "FAIL: something broke")]
