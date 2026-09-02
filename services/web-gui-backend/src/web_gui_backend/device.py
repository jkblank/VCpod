"""Talks to `sync-orchestrator identify-device` as a subprocess, never an
in-process import -- sync-orchestrator is a standalone `uv` project kept
isolated specifically so its `iopenpod`/PyQt6 dependency tree never
merges with this (root-workspace) service's, same reasoning
sync-orchestrator's own `_build_music_stack_fetch_cmd` already documents
for the reverse direction (it shells out to `music-stack fetch` rather
than importing music-stack-cli in-process).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


class DeviceIdentifyError(Exception):
    pass


def _default_sync_orchestrator_dir() -> Path:
    """Absolute path to the sibling sync-orchestrator project, derived
    from this installed package's own location -- same pattern
    sync-orchestrator's own _default_music_stack_project_dir() uses, for
    the same reason (a plain relative default only works when invoked
    from one particular working directory)."""
    return Path(__file__).resolve().parents[3] / "sync-orchestrator"


def identify_connected_devices(
    sync_orchestrator_dir: Path | str | None = None,
) -> list[dict]:
    """Returns every currently-connected iPod's identity (path,
    volume_label, serial, firewire_guid, model_family, generation,
    model_number, capacity), via `sync-orchestrator identify-device`.
    Empty list when nothing's connected -- never raises for that case,
    only for an actual failure to run the subprocess or parse its
    output."""
    project_dir = Path(sync_orchestrator_dir) if sync_orchestrator_dir else _default_sync_orchestrator_dir()

    result = subprocess.run(
        ["uv", "run", "--project", str(project_dir), "sync-orchestrator", "identify-device"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise DeviceIdentifyError(
            f"identify-device failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        payload = json.loads(result.stdout)
        return payload["devices"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise DeviceIdentifyError(f"could not parse identify-device output: {e}") from e
