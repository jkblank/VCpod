from __future__ import annotations

import argparse
import sys

from common.config import ConfigError, load_profile_config

from podcast_manager.api import (
    list_subscriptions,
    load_credentials,
    login,
    resolve_show_selection,
)
from podcast_manager.download import (
    backfill_episode_metadata,
    prune_unsubscribed_shows,
    push_pending_play_status,
    sync_shows,
)


def _cmd_list_subscriptions(args: argparse.Namespace) -> int:
    try:
        email, password = load_credentials(args.credentials_path)
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
        email, password = load_credentials(args.credentials_path)
        token = login(email, password)
        subscriptions = list_subscriptions(token)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: could not authenticate with Pocket Casts: {e}")
        return 1

    # Must run against the full, unfiltered subscriptions list above --
    # before any --show narrowing below, which is a per-run scope choice,
    # not an unsubscribe signal. See prune_unsubscribed_shows's docstring.
    pruned = prune_unsubscribed_shows(subscriptions, state_db_path=args.state_path)
    if pruned:
        shows = sorted({e.show_name for e in pruned})
        print(
            f"Pruned {len(pruned)} episode(s) from {len(shows)} unsubscribed "
            f"show(s): {', '.join(shows)}"
        )

    shows_filter = args.show or profile.podcasts.shows
    if shows_filter != "all":
        subscriptions, unmatched = resolve_show_selection(subscriptions, shows_filter)
        for name in unmatched:
            print(f"WARNING: no subscription matched --show {name!r}")

    if not subscriptions:
        print("No matching subscriptions to sync.")
        return 0

    outcomes = sync_shows(
        subscriptions,
        token=token,
        library_root=args.library_root,
        state_db_path=args.state_path,
        sync_unplayed_only=profile.podcasts.sync_unplayed_only,
        max_episodes_per_show=profile.podcasts.max_episodes_per_show,
        fill_modes=profile.podcasts.fill_modes,
        episode_filter=profile.podcasts.episode_filter,
        delete_played_episodes=profile.podcasts.delete_played_episodes,
    )

    total_downloaded = 0
    total_already = 0
    total_failed = 0
    total_deleted = 0
    shows_with_errors: list[str] = []
    for outcome in outcomes:
        if outcome.error is not None:
            # A per-show API failure (e.g. list_full_episodes timing out)
            # happens before any per-episode handling in sync_podcast, so
            # it isn't covered by that function's own per-episode
            # try/except — must not abort the remaining shows either.
            print(f"{outcome.podcast.title}: ERROR ({outcome.error})")
            shows_with_errors.append(outcome.podcast.title)
            continue

        result = outcome.result
        total_downloaded += len(result.downloaded)
        total_already += len(result.already_present)
        total_failed += len(result.failed)
        total_deleted += len(result.deleted)
        print(
            f"{outcome.podcast.title}: {len(result.downloaded)} downloaded, "
            f"{len(result.already_present)} already present"
            + (f", {len(result.failed)} failed" if result.failed else "")
            + (f", {len(result.deleted)} episode(s) removed" if result.deleted else "")
        )
        for episode, error in result.failed:
            print(f"  FAILED: {episode.title!r} ({error})")

    print(
        f"Total: {total_downloaded} downloaded, {total_already} already present, "
        f"{total_failed} episode(s) failed, {total_deleted} episode(s) removed "
        "(played or over the per-show limit)"
    )
    if shows_with_errors:
        print(f"Shows that could not be reached at all: {', '.join(shows_with_errors)}")
    return 0


def _cmd_push_play_status(args: argparse.Namespace) -> int:
    # Kept as a manual/standalone entrypoint (e.g. to flush pending pushes
    # without running a full podcast sync) — the normal automated path is
    # run_sync calling push_pending_play_status directly, see its docstring.
    try:
        email, password = load_credentials(args.credentials_path)
        token = login(email, password)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: could not authenticate with Pocket Casts: {e}")
        return 1

    pushed, failed = push_pending_play_status(token, state_db_path=args.state_path)
    if not pushed and not failed:
        print("No pending play-state updates.")
        return 0

    print(f"Pushed play state for {len(pushed)} episode(s)")
    if failed:
        print(f"Failed to push {len(failed)} episode(s):")
        for episode, error in failed:
            print(f"  {episode.title or episode.episode_uuid}: {error}")
    return 0


def _cmd_backfill_metadata(args: argparse.Namespace) -> int:
    # One-off: sync's own record_episode() only (re)writes RSS metadata
    # for a show's *current* candidates each run, so an episode that's
    # already played/archived (or otherwise aged out of that window)
    # never gets touched again by a normal sync -- this walks every
    # locally-known episode instead. See backfill_episode_metadata's
    # docstring.
    try:
        email, password = load_credentials(args.credentials_path)
        token = login(email, password)
        subscriptions = list_subscriptions(token)
    except (OSError, ValueError, KeyError) as e:
        print(f"ERROR: could not authenticate with Pocket Casts: {e}")
        return 1

    result = backfill_episode_metadata(subscriptions, state_db_path=args.state_path)

    print(f"Backfilled metadata for {len(result.updated)} episode(s)")
    if result.unmatched:
        print(f"{result.unmatched} episode(s) needed backfill but weren't found in their feed")
    if result.unresolved_feeds:
        print(
            f"Could not resolve a feed for {len(result.unresolved_feeds)} show(s): "
            f"{', '.join(result.unresolved_feeds)}"
        )
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
        help="Restrict sync to this show, by UUID or title (case-insensitive, "
        "repeatable). Defaults to the profile's podcasts.shows config.",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    push_parser = subparsers.add_parser(
        "push-play-status",
        help="Push locally-recorded device play state (from sync-orchestrator's "
        "device read-back) to Pocket Casts",
    )
    push_parser.add_argument("--credentials-path", required=True)
    push_parser.add_argument("--state-path", required=True)
    push_parser.set_defaults(func=_cmd_push_play_status)

    backfill_parser = subparsers.add_parser(
        "backfill-metadata",
        help="One-off: backfill RSS-sourced metadata (description/episode/season "
        "number/published date) for already-downloaded episodes a normal sync "
        "will no longer revisit",
    )
    backfill_parser.add_argument("--credentials-path", required=True)
    backfill_parser.add_argument("--state-path", required=True)
    backfill_parser.set_defaults(func=_cmd_backfill_metadata)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
