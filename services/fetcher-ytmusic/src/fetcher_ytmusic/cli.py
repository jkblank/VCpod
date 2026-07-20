from __future__ import annotations

import argparse
import sys

from common.config import ConfigError, load_profile_config
from common.lock import LockTimeoutError

from fetcher_ytmusic.api import list_playlists
from fetcher_ytmusic.download import DownloadError, fetch_playlist


def _cmd_list_playlists(args: argparse.Namespace) -> int:
    try:
        playlists = list_playlists(args.oauth_path)
    except Exception as e:  # ytmusicapi raises plain Exception on auth failure
        print(f"ERROR: could not authenticate with YouTube Music: {e}")
        return 1

    if not playlists:
        print("No library playlists found.")
        return 0
    for p in playlists:
        owner = p.owner or "-"
        print(f"{p.source_id}\t{p.track_count}\t{owner}\t{p.name}")
    return 0


def _cmd_fetch(args: argparse.Namespace) -> int:
    try:
        profile = load_profile_config(args.profile)
    except ConfigError as e:
        print(f"ERROR {args.profile}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    entry = next(
        (
            p
            for p in profile.playlists
            if p.name == args.playlist and p.source == "ytmusic"
        ),
        None,
    )
    if entry is None:
        print(
            f"ERROR: no ytmusic playlist named {args.playlist!r} "
            f"in profile {profile.profile!r}"
        )
        return 1

    try:
        result = fetch_playlist(
            playlist_name=entry.name,
            playlist_source_id=entry.source_id,
            profile=profile.profile,
            cookies_path=args.cookies_path,
            oauth_path=args.oauth_path,
            library_root=args.library_root,
            playlists_root=args.playlists_root,
            state_db_path=args.state_path,
            lock_path=args.lock_path,
            lock_timeout=args.lock_timeout,
            sync_mode=entry.sync_mode,
        )
    except LockTimeoutError as e:
        print(f"ERROR: {e}")
        return 1
    except (DownloadError, OSError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    print(f"m3u8: {result.m3u8_path}")
    print(f"new tracks: {len(result.new_tracks)}")
    print(f"already known: {len(result.already_known_tracks)}")
    if result.failed_tracks:
        print(f"failed to download: {len(result.failed_tracks)}")
        for meta, error in result.failed_tracks:
            print(f"  {meta.source_id} - {meta.artist} - {meta.title}: {error}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="fetcher-ytmusic")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-playlists", help="List the account's YouTube Music library playlists"
    )
    list_parser.add_argument(
        "--oauth-path", required=True, help="ytmusicapi OAuth token file"
    )
    list_parser.set_defaults(func=_cmd_list_playlists)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Download one playlist from a profile"
    )
    fetch_parser.add_argument("--profile", required=True, help="Path to profile YAML")
    fetch_parser.add_argument("--playlist", required=True, help="Playlist name")
    fetch_parser.add_argument(
        "--cookies-path",
        required=True,
        help="yt-dlp YouTube cookies file (Netscape format) — required for the "
        "actual download; YouTube's bot-check blocks unauthenticated fetches.",
    )
    fetch_parser.add_argument(
        "--oauth-path",
        default=None,
        help="ytmusicapi OAuth token file. Optional: public playlists resolve "
        "fine without it, only needed for the account's own private playlists.",
    )
    fetch_parser.add_argument("--library-root", required=True)
    fetch_parser.add_argument("--playlists-root", required=True)
    fetch_parser.add_argument("--state-path", required=True)
    fetch_parser.add_argument(
        "--lock-path",
        default=None,
        help="Path to the YouTube Music session lock file. Defaults to "
        ".ytmusic.lock next to --state-path.",
    )
    fetch_parser.add_argument(
        "--lock-timeout",
        type=float,
        default=1800,
        help="Max seconds to wait for another session to finish (default 1800).",
    )
    fetch_parser.set_defaults(func=_cmd_fetch)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
