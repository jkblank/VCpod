from __future__ import annotations

from pathlib import Path


def write_m3u8(path: Path | str, track_paths: list[Path | str]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U", *(str(p) for p in track_paths)]
    path.write_text("\n".join(lines) + "\n")
