"""Music source playlist browsing + credential capture (Apple Music,
YouTube Music). Spotify has no route here at all -- shelved, blocked on
a Premium API requirement outside this project's control, same as every
other place in the codebase that touches it.

Cookie capture is deliberately manual paste/upload of an already-
exported cookies.txt, not automated capture -- real cross-origin cookie
reading from a browser is impossible (same-origin policy), and the only
way to actually automate it (a Playwright-driven separate browser
instance) is real, substantial extra scope kept explicitly out of this
build. See notes.md's 2026-09-02 entry."""

from __future__ import annotations

import dataclasses
import tempfile
from http.cookiejar import LoadError, MozillaCookieJar
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from common.config import ConfigError, load_global_config, resolve_config_path

from fetcher_apple.api import list_playlists as list_apple_music_playlists
from fetcher_ytmusic.api import list_playlists as list_ytmusic_playlists

from web_gui_backend.atomic import write_text_atomic
from web_gui_backend.errors import config_error_response

router = APIRouter()

# The one cookie gamdl's own AppleMusicApi.create_from_netscape_cookies
# actually checks (media-user-token on .music.apple.com) -- validating
# for it here catches a wrong/incomplete paste before it ever reaches a
# real fetch attempt. Domain matches gamdl's own APPLE_MUSIC_COOKIE_DOMAIN
# constant; hardcoded rather than imported to avoid reaching into gamdl's
# internal module layout for one stable string.
_APPLE_MUSIC_COOKIE_DOMAIN = ".music.apple.com"
_APPLE_MUSIC_REQUIRED_COOKIE = "media-user-token"


def _global_config(request: Request):
    try:
        return load_global_config(request.app.state.config_root / "global.yaml")
    except ConfigError as e:
        raise config_error_response(e) from e


def _validate_cookies_txt(content: str, *, require_apple_token: bool) -> None:
    """Raises ValueError (caller turns this into a 422) if content isn't
    a usable Netscape-format cookie jar. Loads from a real temp file --
    MozillaCookieJar.load() takes a filename, not a string -- so this
    never touches the real target path unless validation passes."""
    if not content.strip():
        raise ValueError("cookies file is empty")

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write(content)
        tmp_path = Path(f.name)
    try:
        jar = MozillaCookieJar(str(tmp_path))
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except (LoadError, OSError) as e:
            raise ValueError(f"not a valid Netscape-format cookies file: {e}") from e
        if len(jar) == 0:
            raise ValueError("no cookies found in file")
        if require_apple_token:
            has_token = any(
                c.name == _APPLE_MUSIC_REQUIRED_COOKIE and c.domain == _APPLE_MUSIC_COOKIE_DOMAIN
                for c in jar
            )
            if not has_token:
                raise ValueError(
                    f"no {_APPLE_MUSIC_REQUIRED_COOKIE!r} cookie found for "
                    f"{_APPLE_MUSIC_COOKIE_DOMAIN} — make sure this was exported "
                    "from a real, logged-in music.apple.com session"
                )
    finally:
        tmp_path.unlink(missing_ok=True)


@router.get("/api/sources/apple-music/playlists")
def apple_music_playlists(request: Request) -> list[dict]:
    config = _global_config(request)
    cookies_path = resolve_config_path(
        config.sources.apple_music.cookies_file, request.app.state.config_root
    )
    try:
        playlists = list_apple_music_playlists(str(cookies_path))
    except Exception as e:
        # Deliberately broad: this reaches out to gamdl/Apple's real API,
        # whose failure modes (missing file, expired cookies, network,
        # Apple-side errors) aren't enumerable up front -- a clean 502
        # beats a raw 500 leaking a stack trace to the frontend.
        raise HTTPException(
            status_code=502, detail=f"could not list Apple Music playlists: {e}"
        ) from e
    return [dataclasses.asdict(p) for p in playlists]


@router.get("/api/sources/ytmusic/playlists")
def ytmusic_playlists(request: Request) -> list[dict]:
    config = _global_config(request)
    oauth_path = resolve_config_path(
        config.sources.ytmusic.oauth_file, request.app.state.config_root
    )
    try:
        playlists = list_ytmusic_playlists(str(oauth_path))
    except Exception as e:
        raise HTTPException(
            status_code=502, detail=f"could not list YouTube Music playlists: {e}"
        ) from e
    return [dataclasses.asdict(p) for p in playlists]


@router.put("/api/sources/apple-music/cookies")
def put_apple_music_cookies(body: dict, request: Request) -> dict:
    content = body.get("cookies_txt", "")
    try:
        _validate_cookies_txt(content, require_apple_token=True)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    config = _global_config(request)
    target = resolve_config_path(
        config.sources.apple_music.cookies_file, request.app.state.config_root
    )
    write_text_atomic(content, target)
    return {"status": "ok"}


@router.put("/api/sources/ytmusic/cookies")
def put_ytmusic_cookies(body: dict, request: Request) -> dict:
    content = body.get("cookies_txt", "")
    try:
        _validate_cookies_txt(content, require_apple_token=False)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    config = _global_config(request)
    target = resolve_config_path(
        config.sources.ytmusic.cookies_file, request.app.state.config_root
    )
    write_text_atomic(content, target)
    return {"status": "ok"}


@router.get("/api/sources/status")
def sources_status(request: Request) -> dict:
    config = _global_config(request)
    config_root = request.app.state.config_root

    def _file_status(container_path: str) -> dict:
        path = resolve_config_path(container_path, config_root)
        if not path.is_file():
            return {"exists": False, "updated_at": None}
        return {"exists": True, "updated_at": path.stat().st_mtime}

    return {
        "apple_music": {
            "enabled": config.sources.apple_music.enabled,
            **_file_status(config.sources.apple_music.cookies_file),
        },
        "ytmusic": {
            "enabled": config.sources.ytmusic.enabled,
            **_file_status(config.sources.ytmusic.oauth_file),
        },
        "spotify": {
            "enabled": config.sources.spotify.enabled,
            **_file_status(config.sources.spotify.credentials_file),
        },
    }
