from __future__ import annotations

from pathlib import Path

from mutagen.mp4 import MP4, MP4FreeForm

SOURCE_TAG = "----:com.apple.iTunes:source"
SOURCE_ID_TAG = "----:com.apple.iTunes:source_id"


def read_basic_tags(path: Path | str) -> tuple[str, str]:
    audio = MP4(path)
    title = (audio.get("\xa9nam") or [""])[0]
    artist = (audio.get("\xa9ART") or [""])[0]
    return str(title), str(artist)


def add_dedup_tags(path: Path | str, source: str, source_id: str) -> None:
    audio = MP4(path)
    audio[SOURCE_TAG] = [MP4FreeForm(source.encode("utf-8"))]
    audio[SOURCE_ID_TAG] = [MP4FreeForm(source_id.encode("utf-8"))]
    audio.save()


def read_dedup_tags(path: Path | str) -> tuple[str | None, str | None]:
    audio = MP4(path)
    source = _read_freeform(audio, SOURCE_TAG)
    source_id = _read_freeform(audio, SOURCE_ID_TAG)
    return source, source_id


def _read_freeform(audio: MP4, key: str) -> str | None:
    values = audio.get(key)
    if not values:
        return None
    return bytes(values[0]).decode("utf-8")


def set_basic_tags(path: Path | str, *, title: str, artist: str, album: str) -> None:
    audio = MP4(path)
    audio["\xa9nam"] = [title]
    audio["\xa9ART"] = [artist]
    audio["\xa9alb"] = [album]
    audio.save()
