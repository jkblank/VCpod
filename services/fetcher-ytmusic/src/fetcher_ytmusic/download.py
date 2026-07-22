from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from common.lock import FileLock, LockTimeoutError
from common.models import PlaylistEntry
from common.playlist import write_m3u8
from common.state import StateDB, TrackRecord

from fetcher_ytmusic.api import TrackMeta, get_playlist_tracks
from fetcher_ytmusic.tag import add_dedup_tags, set_basic_tags

SOURCE = "ytmusic"
_SCRATCH_DIRNAME = "_ytdlp_scratch"
_ILLEGAL_CHARS_RE = re.compile(r'[\\/:*?"<>|]')


class DownloadError(Exception):
    pass


@dataclass
class FetchResult:
    m3u8_path: Path
    new_tracks: list[TrackRecord] = field(default_factory=list)
    already_known_tracks: list[TrackRecord] = field(default_factory=list)
    failed_tracks: list[tuple[TrackMeta, str]] = field(default_factory=list)


@dataclass
class PlaylistSyncOutcome:
    entry: PlaylistEntry
    result: FetchResult | None
    error: str | None


def _sanitize(text: str) -> str:
    cleaned = _ILLEGAL_CHARS_RE.sub("_", text).strip()
    return cleaned or "Unknown"


def _run_ytdlp_single_track(
    *, video_id: str, cookies_path: Path, scratch_dir: Path
) -> None:
    # Requires: deno on PATH (yt-dlp's JS challenge solver) and the
    # bgutil-ytdlp-pot-provider companion HTTP server running on
    # 127.0.0.1:4416 (yt-dlp auto-detects it once the plugin is
    # installed — no explicit flag needed). Without both, every track
    # fails identically: YouTube's bot-check blocks every real audio
    # format, only thumbnail storyboards resolve. See notes.md.
    result = subprocess.run(
        [
            "yt-dlp",
            "-x",
            "--audio-format", "m4a",
            "--audio-quality", "0",
            "--cookies", str(cookies_path),
            "--remote-components", "ejs:github",
            "-o", str(scratch_dir / "%(id)s.%(ext)s"),
            f"https://music.youtube.com/watch?v={video_id}",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DownloadError(
            f"yt-dlp exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )


def _download_one(meta: TrackMeta, cookies_path: Path, library_root: Path) -> Path:
    """Downloads a single track into its own scratch dir (mirrors
    fetcher_spotify's _download_one — no shared output dir to disambiguate,
    no dependence on yt-dlp's own naming), then moves the single resulting
    file into our own canonical layout and overwrites its tags with the
    clean metadata ytmusicapi already gave us — yt-dlp's own embedded
    metadata comes from the YouTube video's own title/uploader fields,
    which are frequently messy ("Artist - Title (Official Video)") rather
    than the clean title/artist/album ytmusicapi resolves."""
    scratch_dir = library_root / _SCRATCH_DIRNAME / meta.source_id
    try:
        _run_ytdlp_single_track(
            video_id=meta.source_id, cookies_path=cookies_path, scratch_dir=scratch_dir
        )
        found = [p for p in scratch_dir.rglob("*") if p.is_file()]
        if len(found) != 1:
            raise DownloadError(
                f"expected exactly 1 output file, found {len(found)}: {found}"
            )

        dest_dir = library_root / _sanitize(meta.artist) / _sanitize(meta.album)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = _dedupe_path(dest_dir / f"{_sanitize(meta.title)}{found[0].suffix}")
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


def _fetch_per_track(
    *,
    db: StateDB,
    tracks_meta: list[TrackMeta],
    cookies_path: Path,
    library_root: Path,
) -> tuple[list[TrackRecord], list[TrackRecord], list[tuple[TrackMeta, str]], dict[str, Path]]:
    new_tracks: list[TrackRecord] = []
    already_known: list[TrackRecord] = []
    failed_tracks: list[tuple[TrackMeta, str]] = []
    path_by_id: dict[str, Path] = {}

    for meta in tracks_meta:
        existing = db.get_track(SOURCE, meta.source_id)
        if existing is not None:
            already_known.append(existing)
            path_by_id[meta.source_id] = Path(existing.local_path)
            continue

        try:
            dest = _download_one(meta, cookies_path, library_root)
        except DownloadError as e:
            failed_tracks.append((meta, str(e)))
            continue

        set_basic_tags(dest, title=meta.title, artist=meta.artist, album=meta.album)
        add_dedup_tags(dest, SOURCE, meta.source_id)
        record = TrackRecord(
            source=SOURCE,
            source_id=meta.source_id,
            local_path=str(dest),
            title=meta.title,
            artist=meta.artist,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )
        db.record_track(record)
        new_tracks.append(record)
        path_by_id[meta.source_id] = dest

    return new_tracks, already_known, failed_tracks, path_by_id


def fetch_playlist(
    *,
    playlist_name: str,
    playlist_source_id: str,
    profile: str,
    cookies_path: Path | str,
    library_root: Path | str,
    playlists_root: Path | str,
    state_db_path: Path | str,
    oauth_path: Path | str | None = None,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
    sync_mode: str = "absolute",
) -> FetchResult:
    cookies_path = Path(cookies_path)
    # Must be absolute: iOpenPod's playlist-file matching compares .m3u8
    # entries against absolute paths from its own PC-folder scan, so a
    # relative library_root here silently produces paths in the .m3u8
    # that never match anything — confirmed live: every entry in a real
    # playlist synced with a relative --library-root came back
    # skipped_count == total_entries, items == []. Same bug class as
    # fetcher-apple's per-track fallback fix (see notes.md).
    library_root = Path(library_root).resolve()
    playlists_root = Path(playlists_root)
    if lock_path is None:
        lock_path = Path(state_db_path).parent / ".ytmusic.lock"

    tracks_meta = get_playlist_tracks(
        playlist_source_id, oauth_path=str(oauth_path) if oauth_path else None
    )

    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        new_tracks, already_known, failed_tracks, path_by_id = _fetch_per_track(
            db=db,
            tracks_meta=tracks_meta,
            cookies_path=cookies_path,
            library_root=library_root,
        )

    final_paths = [
        path_by_id[m.source_id] for m in tracks_meta if m.source_id in path_by_id
    ]

    m3u8_path = playlists_root / profile / f"{playlist_name}.m3u8"
    write_m3u8(m3u8_path, final_paths, mode=sync_mode)

    return FetchResult(
        m3u8_path=m3u8_path,
        new_tracks=new_tracks,
        already_known_tracks=already_known,
        failed_tracks=failed_tracks,
    )


def fetch_playlists(
    entries: list[PlaylistEntry],
    *,
    profile: str,
    cookies_path: Path | str,
    library_root: Path | str,
    playlists_root: Path | str,
    state_db_path: Path | str,
    oauth_path: Path | str | None = None,
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
) -> list[PlaylistSyncOutcome]:
    """Fetch each entry in turn, same as calling fetch_playlist() once per
    entry, except one entry's failure doesn't stop the rest — matching the
    per-item resilience podcast_manager's sync already has."""
    outcomes: list[PlaylistSyncOutcome] = []
    for entry in entries:
        try:
            result = fetch_playlist(
                playlist_name=entry.name,
                playlist_source_id=entry.source_id,
                profile=profile,
                cookies_path=cookies_path,
                library_root=library_root,
                playlists_root=playlists_root,
                state_db_path=state_db_path,
                oauth_path=oauth_path,
                lock_path=lock_path,
                lock_timeout=lock_timeout,
                sync_mode=entry.sync_mode,
            )
        except (LockTimeoutError, DownloadError, OSError, ValueError) as e:
            outcomes.append(PlaylistSyncOutcome(entry=entry, result=None, error=str(e)))
            continue
        outcomes.append(PlaylistSyncOutcome(entry=entry, result=result, error=None))
    return outcomes
