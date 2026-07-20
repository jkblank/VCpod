from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from common.lock import FileLock
from common.playlist import write_m3u8
from common.state import StateDB, TrackRecord

from fetcher_apple.api import TrackMeta, get_playlist_tracks
from fetcher_apple.tag import add_dedup_tags, read_basic_tags

SOURCE = "apple_music"
_SCRATCH_DIRNAME = "_gamdl_scratch"
_AUDIO_SUFFIXES = {".m4a", ".mp3"}

# gamdl's own URL regex (gamdl/interface/constants.py) only recognizes these
# two catalog playlist id shapes. Apple's personalized/algorithmic "Mix"
# playlists (Chill, New Music, etc.) use a different `pl.pm-*` id that
# matches neither — confirmed live against the installed pattern. This is
# not a formatting quirk to route around: stripping the `pm-` prefix down
# to a shape gamdl *does* accept (`pl.<32-hex>`) gets a clean 404 from
# Apple's own catalog API, so `pm-` is a real, required part of the id.
# See notes.md for the upstream-fix writeup. `get_playlist_tracks` already
# resolves these playlists fine (same catalog endpoint, no URL regex
# involved), so for anything gamdl's CLI can't parse as a whole playlist we
# fall back to downloading track-by-track via individual song URLs
# (`song` type URLs just need a numeric id, which gamdl's regex supports).
_GAMDL_PLAYLIST_ID_RE = re.compile(r"^pl\.[0-9a-z]{32}$|^pl\.u-[a-zA-Z0-9]+$")


def _gamdl_can_parse_playlist_id(source_id: str) -> bool:
    return bool(_GAMDL_PLAYLIST_ID_RE.match(source_id))


class DownloadError(Exception):
    pass


@dataclass
class FetchResult:
    m3u8_path: Path
    new_tracks: list[TrackRecord]
    already_known_tracks: list[TrackRecord]
    unmatched_paths: list[Path]
    failed_tracks: list[TrackMeta]


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _match_track(
    track_path: Path, by_key: dict[tuple[str, str], list[TrackMeta]]
) -> TrackMeta | None:
    title, artist = read_basic_tags(track_path)
    key = (_normalize(title), _normalize(artist))
    candidates = by_key.get(key)
    if not candidates:
        return None
    return candidates.pop(0)


def _dedupe_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _run_gamdl(
    *,
    playlist_url: str,
    cookies_path: Path,
    library_root: Path,
    scratch_dir: Path,
) -> None:
    result = subprocess.run(
        [
            "gamdl",
            playlist_url,
            "--cookies-path", str(cookies_path),
            "--output-path", str(library_root),
            "--save-playlist",
            "--playlist-folder-template", str(scratch_dir.relative_to(library_root)),
            "--playlist-file-template", "playlist",
            "--no-exceptions",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DownloadError(
            f"gamdl exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )


def _parse_gamdl_m3u(m3u_path: Path) -> list[Path | None]:
    if not m3u_path.is_file():
        raise DownloadError(f"gamdl did not produce a playlist file at {m3u_path}")
    paths: list[Path | None] = []
    for line in m3u_path.read_text(encoding="utf8").splitlines():
        line = line.strip()
        if not line:
            paths.append(None)
            continue
        paths.append((m3u_path.parent / line).resolve())
    return paths


def _fetch_via_playlist_url(
    *,
    db: StateDB,
    tracks_meta: list[TrackMeta],
    playlist_source_id: str,
    cookies_path: Path,
    library_root: Path,
    storefront: str,
) -> tuple[list[TrackRecord], list[TrackRecord], list[Path], list[TrackMeta], list[Path]]:
    """The primary path: one gamdl invocation for the whole playlist. Used
    whenever gamdl's own URL regex can parse the playlist id (the common
    case, and the only one validated against real accounts so far)."""
    scratch_dir = library_root / _SCRATCH_DIRNAME / playlist_source_id
    playlist_url = f"https://music.apple.com/{storefront}/playlist/{playlist_source_id}"
    try:
        _run_gamdl(
            playlist_url=playlist_url,
            cookies_path=cookies_path,
            library_root=library_root,
            scratch_dir=scratch_dir,
        )
        track_paths = _parse_gamdl_m3u(scratch_dir / "playlist.m3u")
    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)

    # gamdl's own .m3u line positions don't reliably line up 1:1 with the
    # order tracks were fetched in (skipped/retried tracks can shift later
    # entries) — confirmed by a real mismatch during live testing. Match each
    # downloaded file back to its track by the (title, artist) tags gamdl
    # itself already wrote correctly, instead of trusting list position.
    by_key: dict[tuple[str, str], list[TrackMeta]] = {}
    for meta in tracks_meta:
        key = (_normalize(meta.title), _normalize(meta.artist))
        by_key.setdefault(key, []).append(meta)

    new_tracks: list[TrackRecord] = []
    already_known: list[TrackRecord] = []
    unmatched_paths: list[Path] = []
    final_paths: list[Path] = []

    for track_path in track_paths:
        if track_path is None or not track_path.is_file():
            continue
        final_paths.append(track_path)

        meta = _match_track(track_path, by_key)
        if meta is None:
            unmatched_paths.append(track_path)
            continue

        existing = db.get_track(SOURCE, meta.source_id)
        if existing is not None:
            already_known.append(existing)
            continue

        add_dedup_tags(track_path, SOURCE, meta.source_id)
        record = TrackRecord(
            source=SOURCE,
            source_id=meta.source_id,
            local_path=str(track_path),
            title=meta.title,
            artist=meta.artist,
            downloaded_at=datetime.now(timezone.utc).isoformat(),
        )
        db.record_track(record)
        new_tracks.append(record)

    return new_tracks, already_known, unmatched_paths, [], final_paths


def _run_gamdl_single_track(
    *,
    song_source_id: str,
    cookies_path: Path,
    scratch_dir: Path,
    storefront: str,
) -> None:
    song_url = f"https://music.apple.com/{storefront}/song/_/{song_source_id}"
    result = subprocess.run(
        [
            "gamdl",
            song_url,
            "--cookies-path", str(cookies_path),
            "--output-path", str(scratch_dir),
            "--no-exceptions",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DownloadError(
            f"gamdl exited {result.returncode}\n{result.stdout}\n{result.stderr}"
        )


def _download_single_track(
    meta: TrackMeta, cookies_path: Path, library_root: Path, storefront: str
) -> Path | None:
    scratch_dir = library_root / _SCRATCH_DIRNAME / meta.source_id
    try:
        _run_gamdl_single_track(
            song_source_id=meta.source_id,
            cookies_path=cookies_path,
            scratch_dir=scratch_dir,
            storefront=storefront,
        )
    except DownloadError:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        return None

    audio_files = [
        p for p in scratch_dir.rglob("*") if p.is_file() and p.suffix.lower() in _AUDIO_SUFFIXES
    ]
    if len(audio_files) != 1:
        shutil.rmtree(scratch_dir, ignore_errors=True)
        return None

    src = audio_files[0]
    dest_dir = library_root / src.parent.relative_to(scratch_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _dedupe_path(dest_dir / src.name)
    src.rename(dest)

    lrc = src.with_suffix(".lrc")
    if lrc.exists():
        lrc.rename(dest.with_suffix(".lrc"))

    shutil.rmtree(scratch_dir, ignore_errors=True)
    return dest


def _fetch_per_track(
    *,
    db: StateDB,
    tracks_meta: list[TrackMeta],
    cookies_path: Path,
    library_root: Path,
    storefront: str,
) -> tuple[list[TrackRecord], list[TrackRecord], list[Path], list[TrackMeta], list[Path]]:
    """Fallback path for playlists gamdl's CLI can't parse as a whole (see
    `_GAMDL_PLAYLIST_ID_RE`): download each track individually by its own
    numeric catalog song id, which gamdl's URL regex does support. We
    already know the definitive, ordered track list from `tracks_meta`, so
    we don't depend on gamdl's --save-playlist output at all here — we
    build our own .m3u8 from wherever each track actually landed."""
    new_tracks: list[TrackRecord] = []
    already_known: list[TrackRecord] = []
    failed_tracks: list[TrackMeta] = []
    final_paths: list[Path] = []

    for meta in tracks_meta:
        existing = db.get_track(SOURCE, meta.source_id)
        if existing is not None:
            already_known.append(existing)
            final_paths.append(Path(existing.local_path))
            continue

        dest = _download_single_track(meta, cookies_path, library_root, storefront)
        if dest is None:
            failed_tracks.append(meta)
            continue

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
        final_paths.append(dest)

    return new_tracks, already_known, [], failed_tracks, final_paths


def fetch_playlist(
    *,
    playlist_name: str,
    playlist_source_id: str,
    profile: str,
    cookies_path: Path | str,
    library_root: Path | str,
    playlists_root: Path | str,
    state_db_path: Path | str,
    storefront: str = "us",
    lock_path: Path | str | None = None,
    lock_timeout: float = 1800,
    sync_mode: str = "absolute",
) -> FetchResult:
    cookies_path = Path(cookies_path)
    # Must be absolute: _fetch_via_playlist_url's track paths always come
    # out absolute (via .resolve() on gamdl's .m3u output), but
    # _fetch_per_track's don't do their own resolution — they're built
    # directly from library_root, so a relative library_root here silently
    # produces relative paths in the resulting .m3u8. Confirmed live: two
    # playlists downloaded through the per-track fallback (Chill, New
    # Music — both required it due to their pl.pm-* ids, see notes.md)
    # wrote relative paths, and iOpenPod's playlist-file sync skipped
    # nearly every entry in both because they didn't resolve against the
    # PC folder it was scanning.
    library_root = Path(library_root).resolve()
    playlists_root = Path(playlists_root)
    if lock_path is None:
        lock_path = Path(state_db_path).parent / ".apple_music.lock"

    tracks_meta = get_playlist_tracks(str(cookies_path), playlist_source_id)

    # Apple Music only allows one active session at a time — confirmed
    # live (an in-progress session gets kicked by a second one starting).
    # This covers the whole download operation, not just one gamdl call,
    # since the per-track fallback below can invoke gamdl many times in a
    # row for a single playlist.
    with FileLock(lock_path, timeout=lock_timeout), StateDB(state_db_path) as db:
        if _gamdl_can_parse_playlist_id(playlist_source_id):
            new_tracks, already_known, unmatched_paths, failed_tracks, final_paths = (
                _fetch_via_playlist_url(
                    db=db,
                    tracks_meta=tracks_meta,
                    playlist_source_id=playlist_source_id,
                    cookies_path=cookies_path,
                    library_root=library_root,
                    storefront=storefront,
                )
            )
        else:
            new_tracks, already_known, unmatched_paths, failed_tracks, final_paths = (
                _fetch_per_track(
                    db=db,
                    tracks_meta=tracks_meta,
                    cookies_path=cookies_path,
                    library_root=library_root,
                    storefront=storefront,
                )
            )

    m3u8_path = playlists_root / profile / f"{playlist_name}.m3u8"
    write_m3u8(m3u8_path, final_paths, mode=sync_mode)

    return FetchResult(
        m3u8_path=m3u8_path,
        new_tracks=new_tracks,
        already_known_tracks=already_known,
        unmatched_paths=unmatched_paths,
        failed_tracks=failed_tracks,
    )
