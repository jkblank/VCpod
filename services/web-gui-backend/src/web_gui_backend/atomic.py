"""Shared atomic-write helper -- a bad credential submission must never
leave a corrupt/partial file where a working one used to be. Same
temp-file-then-rename pattern already used in
podcast_manager/download.py::_download_enclosure."""

from __future__ import annotations

from pathlib import Path


def write_text_atomic(content: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(path)
