from fastapi.testclient import TestClient

from web_gui_backend import routers
from web_gui_backend.app import create_app


def _client(tmp_path) -> TestClient:
    config_root = tmp_path / "config"
    (config_root / "profiles").mkdir(parents=True)
    (config_root / "profiles" / "john.yaml").write_text("profile: john\n")
    app = create_app(
        config_root=config_root,
        library_root=tmp_path / "library",
        state_root=tmp_path / "state",
        sync_orchestrator_dir=tmp_path / "sync-orchestrator-project",
    )
    return TestClient(app)


def _parse_sse(text: str) -> list[tuple[str, str]]:
    parsed = []
    for block in text.strip().split("\n\n"):
        if not block:
            continue
        event = None
        data_lines = []
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data_lines.append(line[len("data: "):])
        parsed.append((event, "\n".join(data_lines)))
    return parsed


def test_plan_rejects_missing_profile(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/sync/plan", json={})

    events = _parse_sse(resp.text)
    assert events == [("error", "profile is required")]


def test_plan_rejects_unknown_profile(tmp_path):
    client = _client(tmp_path)

    resp = client.post("/api/sync/plan", json={"profile": "nobody"})

    events = _parse_sse(resp.text)
    assert len(events) == 1
    assert events[0][0] == "error"
    assert "no such file, and no profile named 'nobody'" in events[0][1]


def test_plan_streams_progress_then_result(monkeypatch, tmp_path):
    async def fake_stream_sync(*, args, sync_orchestrator_dir):
        yield ("progress", "  == Finding device ==")
        yield ("progress", "  scanning...")
        yield ("result", '{"to_add_count": 5}')

    monkeypatch.setattr(routers.sync, "stream_sync", fake_stream_sync)
    client = _client(tmp_path)

    resp = client.post("/api/sync/plan", json={"profile": "john"})

    events = _parse_sse(resp.text)
    assert events == [
        ("progress", "  == Finding device =="),
        ("progress", "  scanning..."),
        ("result", '{"to_add_count": 5}'),
    ]


def test_plan_never_passes_execute_flag(monkeypatch, tmp_path):
    captured = {}

    async def fake_stream_sync(*, args, sync_orchestrator_dir):
        captured["args"] = args
        yield ("result", "{}")

    monkeypatch.setattr(routers.sync, "stream_sync", fake_stream_sync)
    client = _client(tmp_path)

    client.post("/api/sync/plan", json={"profile": "john", "skip_backup": True})

    assert "--execute" not in captured["args"]
    assert "--allow-removals" not in captured["args"]
    assert "--skip-backup" in captured["args"]
    assert str(tmp_path / "config" / "profiles" / "john.yaml") in captured["args"]
    assert str(tmp_path / "library") in captured["args"]
    assert str(tmp_path / "state") in captured["args"]


def test_execute_passes_execute_flag_but_not_allow_removals_by_default(monkeypatch, tmp_path):
    captured = {}

    async def fake_stream_sync(*, args, sync_orchestrator_dir):
        captured["args"] = args
        yield ("result", "{}")

    monkeypatch.setattr(routers.sync, "stream_sync", fake_stream_sync)
    client = _client(tmp_path)

    client.post("/api/sync/execute", json={"profile": "john"})

    assert "--execute" in captured["args"]
    assert "--allow-removals" not in captured["args"]


def test_execute_passes_allow_removals_when_requested(monkeypatch, tmp_path):
    captured = {}

    async def fake_stream_sync(*, args, sync_orchestrator_dir):
        captured["args"] = args
        yield ("result", "{}")

    monkeypatch.setattr(routers.sync, "stream_sync", fake_stream_sync)
    client = _client(tmp_path)

    # Confirms /api/sync/execute never requires a prior /api/sync/plan
    # call -- allow_removals is just a body field, sent directly.
    client.post("/api/sync/execute", json={"profile": "john", "allow_removals": True})

    assert "--execute" in captured["args"]
    assert "--allow-removals" in captured["args"]


def test_execute_passes_sync_orchestrator_dir_from_app_state(monkeypatch, tmp_path):
    captured = {}

    async def fake_stream_sync(*, args, sync_orchestrator_dir):
        captured["dir"] = sync_orchestrator_dir
        yield ("result", "{}")

    monkeypatch.setattr(routers.sync, "stream_sync", fake_stream_sync)
    client = _client(tmp_path)

    client.post("/api/sync/execute", json={"profile": "john"})

    assert str(captured["dir"]) == str(tmp_path / "sync-orchestrator-project")


# --- /api/sync/status -------------------------------------------------


def test_status_not_running_when_no_lock_file(tmp_path):
    client = _client(tmp_path)

    resp = client.get("/api/sync/status", params={"profile": "john"})

    assert resp.status_code == 200
    assert resp.json() == {"running": False, "log_tail": None}


def test_status_running_when_lock_held(tmp_path):
    import fcntl

    client = _client(tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    lock_path = state_root / ".sync_john.lock"
    fd = open(lock_path, "a")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        resp = client.get("/api/sync/status", params={"profile": "john"})
        assert resp.json()["running"] is True
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_status_includes_recent_auto_sync_log_tail_when_running(tmp_path):
    import fcntl

    client = _client(tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "auto-sync.log").write_text("== Matched profile 'john' ==\nsyncing...\n")
    lock_path = state_root / ".sync_john.lock"
    fd = open(lock_path, "a")
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        resp = client.get("/api/sync/status", params={"profile": "john"})
        body = resp.json()
        assert body["running"] is True
        assert body["log_tail"] == ["== Matched profile 'john' ==", "syncing..."]
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


def test_status_no_log_tail_when_not_running(tmp_path):
    client = _client(tmp_path)
    state_root = tmp_path / "state"
    state_root.mkdir(parents=True, exist_ok=True)
    (state_root / "auto-sync.log").write_text("stale content\n")

    resp = client.get("/api/sync/status", params={"profile": "john"})

    assert resp.json() == {"running": False, "log_tail": None}
