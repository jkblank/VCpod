from __future__ import annotations

import argparse
import json
import sys

import httpx

from common.config import ConfigError, load_profile_config

from podcast_manager.api import list_subscriptions, login
from podcast_manager.download import sync_podcast


def _load_credentials(path: str) -> tuple[str, str]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["email"], data["password"]


def _cmd_list_subscriptions(args: argparse.Namespace) -> int:
    try:
        email, password = _load_credentials(args.credentials_path)
        token = login(email, password)
        podcasts = list_subscriptions(token)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: could not authenticate with Pocket Casts: {e}")
        return 1

    if not podcasts:
        print("No subscriptions found.")
        return 0
    for p in podcasts:
        print(f"{p.uuid}\t{p.author}\t{p.title}")
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    try:
        profile = load_profile_config(args.profile)
    except ConfigError as e:
        print(f"ERROR {args.profile}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    try:
        email, password = _load_credentials(args.credentials_path)
        token = login(email, password)
        subscriptions = list_subscriptions(token)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: could not authenticate with Pocket Casts: {e}")
        return 1

    shows_filter = args.show or profile.podcasts.shows
    if shows_filter != "all":
        wanted = set(shows_filter)
        subscriptions = [p for p in subscriptions if p.uuid in wanted]

    if not subscriptions:
        print("No matching subscriptions to sync.")
        return 0

    total_downloaded = 0
    total_already = 0
    total_failed = 0
    shows_with_errors: list[str] = []
    for podcast in subscriptions:
        try:
            result = sync_podcast(
                podcast=podcast,
                token=token,
                library_root=args.library_root,
                state_db_path=args.state_path,
                sync_unplayed_only=profile.podcasts.sync_unplayed_only,
                max_episodes_per_show=profile.podcasts.max_episodes_per_show,
            )
        except (httpx.HTTPError, OSError) as e:
            # A per-show API failure (e.g. list_full_episodes timing out)
            # happens before any per-episode handling in sync_podcast, so
            # it isn't covered by that function's own per-episode
            # try/except — must not abort the remaining shows either.
            print(f"{podcast.title}: ERROR ({e})")
            shows_with_errors.append(podcast.title)
            continue

        total_downloaded += len(result.downloaded)
        total_already += len(result.already_present)
        total_failed += len(result.failed)
        print(
            f"{podcast.title}: {len(result.downloaded)} downloaded, "
            f"{len(result.already_present)} already present"
            + (f", {len(result.failed)} failed" if result.failed else "")
        )
        for episode, error in result.failed:
            print(f"  FAILED: {episode.title!r} ({error})")

    print(
        f"Total: {total_downloaded} downloaded, {total_already} already present, "
        f"{total_failed} episode(s) failed"
    )
    if shows_with_errors:
        print(f"Shows that could not be reached at all: {', '.join(shows_with_errors)}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="podcast-manager")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser(
        "list-subscriptions", help="List the account's Pocket Casts subscriptions"
    )
    list_parser.add_argument("--credentials-path", required=True)
    list_parser.set_defaults(func=_cmd_list_subscriptions)

    sync_parser = subparsers.add_parser(
        "sync", help="Download unplayed episodes for a profile's subscribed shows"
    )
    sync_parser.add_argument("--profile", required=True, help="Path to profile YAML")
    sync_parser.add_argument("--credentials-path", required=True)
    sync_parser.add_argument("--library-root", required=True)
    sync_parser.add_argument("--state-path", required=True)
    sync_parser.add_argument(
        "--show",
        action="append",
        help="Restrict sync to this show UUID (repeatable). Defaults to the "
        "profile's podcasts.shows config.",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
