from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from common.playlist import write_m3u8
from common.state import StateDB, TrackRecord

from fetcher_spotify.api import TrackMeta, get_playlist_tracks
from fetcher_spotify.tag import add_dedup_tags, add_isrc_tag

SOURCE = "spotify"
_SCRATCH_DIRNAME = "_zotify_scratch"
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


class DownloadError(Exception):
    pass


@dataclass
class FetchResult:
    m3u8_path: Path
    new_tracks: list[TrackRecord]
    already_known_tracks: list[TrackRecord]
    unmatched_tracks: list[TrackMeta]


def _sanitize(text: str) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", text).strip()
    return cleaned or "Unknown"


def _run_zotify_single_track(
    *, track_id: str, credentials_path: Path, scratch_dir: Path
) -> None:
    # Flags match Googolplexed0/zotify's CLI (see notes.md for the
    # migration from the abandoned zotify-dev/zotify): --credentials
    # became --creds, --album-library became --root-path, --audio-format
    # became --codec. --output-album is left at its default nested
    # template rather than forced flat — _download_one below finds the
    # single output file via rglob() regardless of where it lands under
    # scratch_dir.
    result = subprocess.run(
        [
            "zotify",
            f"https://open.spotify.com/track/{track_id}",
            "--creds", str(credentials_path),
            "--root-path", str(scratch_dir),
            "--codec", "mp3",
            "-ns",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DownloadError(
            f"zotify exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )


def _download_one(
    meta: TrackMeta, credentials_path: Path, library_root: Path
) -> Path | None:
    """Downloads a single track into its own scratch dir (so there's no
    ambiguity about which file is which — no shared output dir, no relying
    on any placeholder zotify's output template happens to support), then
    moves the single resulting file to our own canonical layout using
    metadata we already know is correct."""
    scratch_dir = library_root / _SCRATCH_DIRNAME / meta.source_id
    try:
        _run_zotify_single_track(
            track_id=meta.source_id,
            credentials_path=credentials_path,
            scratch_dir=scratch_dir,
        )
        found = [p for p in scratch_dir.rglob("*") if p.is_file()]
        if len(found) != 1:
            return None

        dest_dir = library_root / _sanitize(meta.artist) / _sanitize(meta.album)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _dedupe_path(
            dest_dir / f"{meta.track_number:02d} {_sanitize(meta.title)}{found[0].suffix}"
        )
        found[0].rename(dest)
        return dest
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def fetch_playlist(
    *,
    playlist_name: str,
    playlist_source_id: str,
    profile: str,
    credentials_path: Path | str,
    library_root: Path | str,
    playlists_root: Path | str,
    state_db_path: Path | str,
    sync_mode: str = "absolute",
) -> FetchResult:
    credentials_path = Path(credentials_path)
    library_root = Path(library_root)
    playlists_root = Path(playlists_root)

    tracks_meta = get_playlist_tracks(str(credentials_path), playlist_source_id)

    new_tracks: list[TrackRecord] = []
    already_known: list[TrackRecord] = []
    unmatched: list[TrackMeta] = []
    path_by_id: dict[str, Path] = {}

    with StateDB(state_db_path) as db:
        for meta in tracks_meta:
            existing = db.get_track(SOURCE, meta.source_id)
            if existing is not None:
                already_known.append(existing)
                path_by_id[meta.source_id] = Path(existing.local_path)
                continue

            final_path = _download_one(meta, credentials_path, library_root)
            if final_path is None:
                unmatched.append(meta)
                continue

            path_by_id[meta.source_id] = final_path
            add_dedup_tags(final_path, SOURCE, meta.source_id)
            if meta.isrc:
                add_isrc_tag(final_path, meta.isrc)
            record = TrackRecord(
                source=SOURCE,
                source_id=meta.source_id,
                local_path=str(final_path),
                title=meta.title,
                artist=meta.artist,
                downloaded_at=datetime.now(timezone.utc).isoformat(),
            )
            db.record_track(record)
            new_tracks.append(record)

    final_paths = [
        path_by_id[m.source_id] for m in tracks_meta if m.source_id in path_by_id
    ]

    m3u8_path = playlists_root / profile / f"{playlist_name}.m3u8"
    write_m3u8(m3u8_path, final_paths, mode=sync_mode)

    return FetchResult(
        m3u8_path=m3u8_path,
        new_tracks=new_tracks,
        already_known_tracks=already_known,
        unmatched_tracks=unmatched,
    )
