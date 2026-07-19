from __future__ import annotations

from pathlib import Path

from mutagen.id3 import ID3, TSRC, TXXX

SOURCE_DESC = "source"
SOURCE_ID_DESC = "source_id"


def add_dedup_tags(path: Path | str, source: str, source_id: str) -> None:
    tags = ID3(path)
    tags.setall(f"TXXX:{SOURCE_DESC}", [TXXX(encoding=3, desc=SOURCE_DESC, text=[source])])
    tags.setall(
        f"TXXX:{SOURCE_ID_DESC}", [TXXX(encoding=3, desc=SOURCE_ID_DESC, text=[source_id])]
    )
    tags.save()


def read_dedup_tags(path: Path | str) -> tuple[str | None, str | None]:
    tags = ID3(path)
    return _read_txxx(tags, SOURCE_DESC), _read_txxx(tags, SOURCE_ID_DESC)


def add_isrc_tag(path: Path | str, isrc: str) -> None:
    tags = ID3(path)
    tags.setall("TSRC", [TSRC(encoding=3, text=[isrc])])
    tags.save()


def read_isrc_tag(path: Path | str) -> str | None:
    tags = ID3(path)
    frames = tags.getall("TSRC")
    if not frames:
        return None
    return str(frames[0].text[0])


def _read_txxx(tags: ID3, desc: str) -> str | None:
    frames = tags.getall(f"TXXX:{desc}")
    if not frames:
        return None
    return str(frames[0].text[0])
