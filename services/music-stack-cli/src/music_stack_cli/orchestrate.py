from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import httpx

from common.models import GlobalConfig, PlaylistEntry, ProfileConfig

from fetcher_apple.download import (
    DownloadError as AppleDownloadError,
    PlaylistSyncOutcome as ApplePlaylistSyncOutcome,
    fetch_playlists as fetch_apple_playlists,
)
from fetcher_ytmusic.download import (
    DownloadError as YtDownloadError,
    PlaylistSyncOutcome as YtPlaylistSyncOutcome,
    fetch_playlists as fetch_ytmusic_playlists,
)
from podcast_manager.api import list_subscriptions, load_credentials, login, resolve_show_selection
from podcast_manager.download import ShowSyncOutcome, sync_shows

SUPPORTED_SOURCES = ("apple_music", "ytmusic", "podcasts")
UNSUPPORTED_SOURCES = ("spotify",)


def resolve_config_path(container_path: str, config_root: Path) -> Path:
    """global.yaml / profile YAML credential paths are always written as
    /config/... container paths, per the ./config:/config:ro mount in
    docker-compose.yml. Re-root them under config_root so the same YAML
    values work unmodified when run bare-metal. Falls back to the literal
    path if it isn't /config-rooted (e.g. someone already wrote a real host
    path there)."""
    posix_path = PurePosixPath(container_path)
    try:
        return config_root / posix_path.relative_to("/config")
    except ValueError:
        return Path(container_path)


@dataclass
class Roots:
    music_library_root: Path
    playlists_root: Path
    podcasts_library_root: Path
    state_db_path: Path


def resolve_roots(library_root: Path, state_root: Path, profile_name: str) -> Roots:
    return Roots(
        music_library_root=library_root / "music",
        playlists_root=library_root / "playlists",
        podcasts_library_root=library_root / "podcasts",
        state_db_path=state_root / f"{profile_name}.sqlite",
    )


def select_playlists(
    playlists: list[PlaylistEntry], sources: set[str], names: list[str] | None
) -> tuple[list[PlaylistEntry], list[str]]:
    """Filter profile playlists to `sources`, then to `names` if given
    (exact name match). Returns (matched, unmatched `names` entries)."""
    candidates = [p for p in playlists if p.source in sources]
    if not names:
        return candidates, []
    wanted = set(names)
    matched = [p for p in candidates if p.name in wanted]
    matched_names = {p.name for p in matched}
    unmatched = [name for name in names if name not in matched_names]
    return matched, unmatched


@dataclass
class SyncAllResult:
    apple_outcomes: list[ApplePlaylistSyncOutcome] = field(default_factory=list)
    ytmusic_outcomes: list[YtPlaylistSyncOutcome] = field(default_factory=list)
    podcast_outcomes: list[ShowSyncOutcome] = field(default_factory=list)
    unmatched_playlists: list[str] = field(default_factory=list)
    unmatched_shows: list[str] = field(default_factory=list)
    source_errors: list[str] = field(default_factory=list)


def run_sync(
    *,
    profile: ProfileConfig,
    global_config: GlobalConfig,
    config_root: Path,
    roots: Roots,
    sources: set[str],
    playlist_names: list[str] | None,
    show_selectors: list[str] | None,
    storefront: str = "us",
    lock_timeout: float = 1800,
) -> SyncAllResult:
    result = SyncAllResult()

    for source in sources:
        if source not in SUPPORTED_SOURCES:
            if source in UNSUPPORTED_SOURCES:
                result.source_errors.append(
                    f"{source}: not supported by this command yet (fetcher-spotify is a "
                    "standalone package, and downloads are currently blocked on a Premium "
                    "requirement anyway — see notes.md)"
                )
            else:
                result.source_errors.append(f"{source}: unknown source")

    music_sources = sources & {"apple_music", "ytmusic"}
    if music_sources:
        # Select once across every active music source, not per-source —
        # otherwise a playlist that matches under apple_music would be
        # wrongly reported as unmatched too when ytmusic is also selected,
        # since ytmusic's own candidate set never contained it to begin with.
        selected, unmatched = select_playlists(profile.playlists, music_sources, playlist_names)
        result.unmatched_playlists.extend(unmatched)

        apple_entries = [p for p in selected if p.source == "apple_music"]
        if apple_entries:
            cookies_path = resolve_config_path(
                global_config.sources.apple_music.cookies_file, config_root
            )
            try:
                result.apple_outcomes = fetch_apple_playlists(
                    apple_entries,
                    profile=profile.profile,
                    cookies_path=cookies_path,
                    library_root=roots.music_library_root,
                    playlists_root=roots.playlists_root,
                    state_db_path=roots.state_db_path,
                    storefront=storefront,
                    lock_timeout=lock_timeout,
                )
            except (AppleDownloadError, OSError, ValueError) as e:
                result.source_errors.append(f"apple_music: could not authenticate ({e})")

        ytmusic_entries = [p for p in selected if p.source == "ytmusic"]
        if ytmusic_entries:
            cookies_path = resolve_config_path(
                global_config.sources.ytmusic.cookies_file, config_root
            )
            oauth_path = resolve_config_path(
                global_config.sources.ytmusic.oauth_file, config_root
            )
            try:
                result.ytmusic_outcomes = fetch_ytmusic_playlists(
                    ytmusic_entries,
                    profile=profile.profile,
                    cookies_path=cookies_path,
                    library_root=roots.music_library_root,
                    playlists_root=roots.playlists_root,
                    state_db_path=roots.state_db_path,
                    oauth_path=oauth_path,
                    lock_timeout=lock_timeout,
                )
            except (YtDownloadError, OSError, ValueError) as e:
                result.source_errors.append(f"ytmusic: could not authenticate ({e})")

    if "podcasts" in sources:
        credentials_path = resolve_config_path(
            profile.podcasts.pocketcasts.credentials_file, config_root
        )
        try:
            email, password = load_credentials(credentials_path)
            token = login(email, password)
            subscriptions = list_subscriptions(token)
        except (OSError, ValueError, KeyError, httpx.HTTPError) as e:
            result.source_errors.append(f"podcasts: could not authenticate ({e})")
        else:
            shows_filter = show_selectors or profile.podcasts.shows
            if shows_filter != "all":
                subscriptions, unmatched = resolve_show_selection(subscriptions, shows_filter)
                result.unmatched_shows.extend(unmatched)
            if subscriptions:
                result.podcast_outcomes = sync_shows(
                    subscriptions,
                    token=token,
                    library_root=roots.podcasts_library_root,
                    state_db_path=roots.state_db_path,
                    sync_unplayed_only=profile.podcasts.sync_unplayed_only,
                    max_episodes_per_show=profile.podcasts.max_episodes_per_show,
                    fill_modes=profile.podcasts.fill_modes,
                )

    return result
