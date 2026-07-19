from __future__ import annotations

import argparse
import sys

from common.config import ConfigError, load_profile_config

from fetcher_spotify.api import list_playlists
from fetcher_spotify.download import DownloadError, fetch_playlist


def _cmd_list_playlists(args: argparse.Namespace) -> int:
    try:
        playlists = list_playlists(args.credentials_path)
    except (OSError, ValueError) as e:
        print(f"ERROR: could not authenticate with Spotify: {e}")
        return 1

    if not playlists:
        print("No playlists found.")
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
            if p.name == args.playlist and p.source == "spotify"
        ),
        None,
    )
    if entry is None:
        print(
            f"ERROR: no spotify playlist named {args.playlist!r} "
            f"in profile {profile.profile!r}"
        )
        return 1

    try:
        result = fetch_playlist(
            playlist_name=entry.name,
            playlist_source_id=entry.source_id,
            profile=profile.profile,
            credentials_path=args.credentials_path,
            library_root=args.library_root,
            playlists_root=args.playlists_root,
            state_db_path=args.state_path,
        )
    except (DownloadError, OSError, ValueError) as e:
        print(f"ERROR: {e}")
        return 1

    print(f"m3u8: {result.m3u8_path}")
    print(f"new tracks: {len(result.new_tracks)}")
    print(f"already known: {len(result.already_known_tracks)}")
    if result.unmatched_tracks:
        print(f"unmatched (not downloaded): {len(result.unmatched_tracks)}")
        for t in result.unmatched_tracks:
            print(f"  {t.source_id} - {t.artist} - {t.title}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="fetcher-spotify")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-playlists", help="List the account's Spotify playlists"
    )
    list_parser.add_argument("--credentials-path", required=True)
    list_parser.set_defaults(func=_cmd_list_playlists)

    fetch_parser = subparsers.add_parser(
        "fetch", help="Download one playlist from a profile"
    )
    fetch_parser.add_argument("--profile", required=True, help="Path to profile YAML")
    fetch_parser.add_argument("--playlist", required=True, help="Playlist name")
    fetch_parser.add_argument("--credentials-path", required=True)
    fetch_parser.add_argument("--library-root", required=True)
    fetch_parser.add_argument("--playlists-root", required=True)
    fetch_parser.add_argument("--state-path", required=True)
    fetch_parser.set_defaults(func=_cmd_fetch)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
