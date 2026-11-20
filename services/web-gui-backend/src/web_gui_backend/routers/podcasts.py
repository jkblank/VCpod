"""Per-profile Pocket Casts subscription browsing + credential capture.

Unlike Apple Music/YouTube Music (one shared household login in
global.yaml), Pocket Casts credentials are per-profile
(config/secrets/pocketcasts/<profile>.json) -- matches
ProfilePocketCastsConfig.credentials_file already being a per-profile
field, not a global.yaml one."""

from __future__ import annotations

import dataclasses
import json

from fastapi import APIRouter, HTTPException, Request

from common.config import ConfigError, load_profile_config, resolve_config_path
from common.state import StateDB

from podcast_manager.api import list_subscriptions, load_credentials, login
from podcast_manager.download import delete_one_off_episode, download_one_off_episode
from podcast_manager.rss import RssEpisodeMeta, fetch_rss_episodes, search_podcasts

from web_gui_backend.atomic import write_text_atomic

router = APIRouter()


def _profile(request: Request, name: str):
    path = request.app.state.config_root / "profiles" / f"{name}.yaml"
    try:
        return load_profile_config(path)
    except ConfigError as e:
        raise HTTPException(status_code=404, detail=f"no profile named {name!r}") from e


@router.get("/api/profiles/{name}/pocketcasts-status")
def pocketcasts_status(name: str, request: Request) -> dict:
    profile = _profile(request, name)
    creds_path = resolve_config_path(
        profile.podcasts.pocketcasts.credentials_file, request.app.state.config_root
    )
    if not creds_path.is_file():
        return {"exists": False, "updated_at": None}
    return {"exists": True, "updated_at": creds_path.stat().st_mtime}


@router.get("/api/profiles/{name}/pocketcasts/subscriptions")
def pocketcasts_subscriptions(name: str, request: Request) -> list[dict]:
    profile = _profile(request, name)
    creds_path = resolve_config_path(
        profile.podcasts.pocketcasts.credentials_file, request.app.state.config_root
    )
    if not creds_path.is_file():
        raise HTTPException(
            status_code=502,
            detail="Pocket Casts credentials not saved yet for this profile",
        )
    try:
        email, password = load_credentials(creds_path)
        token = login(email, password)
        subscriptions = list_subscriptions(token)
    except Exception as e:
        # Broad on purpose -- a saved-but-now-stale password, a Pocket
        # Casts API hiccup, a malformed credentials file are all real,
        # non-enumerable failure modes at this boundary; see sources.py's
        # matching comment on its own list routes.
        raise HTTPException(
            status_code=502, detail=f"could not list Pocket Casts subscriptions: {e}"
        ) from e
    return [dataclasses.asdict(s) for s in subscriptions]


@router.put("/api/profiles/{name}/pocketcasts-credentials")
def put_pocketcasts_credentials(name: str, body: dict, request: Request) -> dict:
    email = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        raise HTTPException(status_code=422, detail="email and password are both required")

    # Cheap, local check first -- no point making a real network call to
    # Pocket Casts for a profile name that doesn't even exist.
    profile = _profile(request, name)

    try:
        # Validates by actually logging in *before* writing anything --
        # a bad credential pair never gets saved, unlike a plain "does
        # this parse as JSON" check.
        login(email, password)
    except Exception as e:
        raise HTTPException(
            status_code=422, detail=f"Pocket Casts rejected these credentials: {e}"
        ) from e

    creds_path = resolve_config_path(
        profile.podcasts.pocketcasts.credentials_file, request.app.state.config_root
    )
    write_text_atomic(json.dumps({"email": email, "password": password}), creds_path)
    return {"status": "ok"}


# --- Search + single-episode download for shows NOT in the account's
# Pocket Casts subscriptions. Deliberately independent of everything
# above: no Pocket Casts login, no credentials file, no profile-scoped
# subscription state -- search is powered by Apple's key-free iTunes
# Search API (same one podcast_manager.rss already uses for best-effort
# feed resolution), and the resulting episode is downloaded straight
# from its own RSS enclosure. See podcast-manager/README.md and
# notes.md for why one-off episodes use a synthetic per-episode
# podcast_uuid instead of being grouped by show.


@router.get("/api/podcasts/search")
def podcasts_search(q: str) -> list[dict]:
    q = q.strip()
    if not q:
        return []
    try:
        results = search_podcasts(q)
    except Exception as e:
        # Broad on purpose -- a real iTunes Search API hiccup is a
        # genuine, non-enumerable failure at this boundary, same
        # reasoning as pocketcasts_subscriptions' own except above.
        raise HTTPException(status_code=502, detail=f"could not search podcasts: {e}") from e
    return [dataclasses.asdict(r) for r in results]


@router.get("/api/podcasts/episodes")
def podcasts_episodes(feed_url: str) -> list[dict]:
    # fetch_rss_episodes never raises (best-effort, returns [] on any
    # fetch/parse failure) -- an unresolvable feed and a genuinely empty
    # show both just come back as [] here, matching that module's own
    # contract rather than inventing a distinction it doesn't make.
    episodes = fetch_rss_episodes(feed_url)
    return [dataclasses.asdict(e) for e in episodes]


@router.post("/api/profiles/{name}/podcasts/one-off")
def post_one_off_download(name: str, body: dict, request: Request) -> dict:
    _profile(request, name)  # 404s for an unknown profile before touching anything

    show_title = (body.get("show_title") or "").strip()
    feed_url = (body.get("feed_url") or "").strip()
    episode_body = body.get("episode") or {}
    enclosure_url = (episode_body.get("enclosure_url") or "").strip()
    if not show_title or not feed_url or not enclosure_url:
        raise HTTPException(
            status_code=422,
            detail="show_title, feed_url, and episode.enclosure_url are all required",
        )

    episode = RssEpisodeMeta(
        enclosure_url=enclosure_url,
        title=episode_body.get("title") or "",
        description=episode_body.get("description") or "",
        episode_number=episode_body.get("episode_number"),
        season_number=episode_body.get("season_number"),
        published=episode_body.get("published"),
        duration_seconds=episode_body.get("duration_seconds"),
    )

    # podcasts/ subdirectory of the shared library root -- same
    # convention music_stack_cli.orchestrate.resolve_roots uses
    # (podcasts_library_root = library_root / "podcasts").
    library_root = request.app.state.library_root / "podcasts"
    state_db_path = request.app.state.state_root / f"{name}.sqlite"
    try:
        record = download_one_off_episode(
            show_title=show_title,
            feed_url=feed_url,
            episode=episode,
            library_root=library_root,
            state_db_path=state_db_path,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"could not download episode: {e}") from e
    return dataclasses.asdict(record)


@router.get("/api/profiles/{name}/podcasts/one-off")
def get_one_off_episodes(name: str, request: Request) -> list[dict]:
    _profile(request, name)
    state_db_path = request.app.state.state_root / f"{name}.sqlite"
    with StateDB(state_db_path) as db:
        episodes = [e for e in db.list_episodes() if e.one_off]
    return [dataclasses.asdict(e) for e in episodes]


@router.delete("/api/profiles/{name}/podcasts/one-off/{episode_uuid}")
def delete_one_off(name: str, episode_uuid: str, request: Request) -> dict:
    _profile(request, name)
    state_db_path = request.app.state.state_root / f"{name}.sqlite"
    deleted = delete_one_off_episode(episode_uuid, state_db_path=state_db_path)
    if deleted is None:
        raise HTTPException(
            status_code=404,
            detail=f"no one-off episode {episode_uuid!r} for profile {name!r}",
        )
    return {"status": "ok"}
