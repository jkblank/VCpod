from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common.config import ConfigError, load_global_config, load_profile_config

from music_stack_cli.orchestrate import (
    SUPPORTED_SOURCES,
    UNSUPPORTED_SOURCES,
    resolve_roots,
    run_sync,
)


def _print_apple_outcome(outcome) -> tuple[int, int, int]:
    entry = outcome.entry
    if outcome.error is not None:
        print(f"[apple_music] {entry.name}: ERROR ({outcome.error})")
        return 0, 0, 0
    result = outcome.result
    print(
        f"[apple_music] {entry.name}: {len(result.new_tracks)} new, "
        f"{len(result.already_known_tracks)} already known"
        + (f", {len(result.failed_tracks)} failed" if result.failed_tracks else "")
    )
    return len(result.new_tracks), len(result.already_known_tracks), len(result.failed_tracks)


def _print_ytmusic_outcome(outcome) -> tuple[int, int, int]:
    entry = outcome.entry
    if outcome.error is not None:
        print(f"[ytmusic] {entry.name}: ERROR ({outcome.error})")
        return 0, 0, 0
    result = outcome.result
    print(
        f"[ytmusic] {entry.name}: {len(result.new_tracks)} new, "
        f"{len(result.already_known_tracks)} already known"
        + (f", {len(result.failed_tracks)} failed" if result.failed_tracks else "")
    )
    return len(result.new_tracks), len(result.already_known_tracks), len(result.failed_tracks)


def _print_podcast_outcome(outcome) -> tuple[int, int, int]:
    podcast = outcome.podcast
    if outcome.error is not None:
        print(f"[podcasts] {podcast.title}: ERROR ({outcome.error})")
        return 0, 0, 0
    result = outcome.result
    print(
        f"[podcasts] {podcast.title}: {len(result.downloaded)} downloaded, "
        f"{len(result.already_present)} already present"
        + (f", {len(result.failed)} failed" if result.failed else "")
    )
    return len(result.downloaded), len(result.already_present), len(result.failed)


def _cmd_sync(args: argparse.Namespace) -> int:
    try:
        global_config = load_global_config(args.global_config)
    except ConfigError as e:
        print(f"ERROR {args.global_config}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    try:
        profile = load_profile_config(args.profile)
    except ConfigError as e:
        print(f"ERROR {args.profile}")
        for line in e.errors:
            print(f"  {line}")
        return 1

    config_root = Path(args.config_root) if args.config_root else Path(args.global_config).resolve().parent
    library_root = Path(args.library_root) if args.library_root else config_root.parent / "library"
    state_root = Path(args.state_root) if args.state_root else config_root.parent / "state"
    roots = resolve_roots(library_root, state_root, profile.profile)

    sources = set(args.source) if args.source else set(SUPPORTED_SOURCES)

    result = run_sync(
        profile=profile,
        global_config=global_config,
        config_root=config_root,
        roots=roots,
        sources=sources,
        playlist_names=args.playlist,
        show_selectors=args.show,
        storefront=args.storefront,
        lock_timeout=args.lock_timeout,
    )

    total_new = total_known = total_failed = 0
    for outcome in result.apple_outcomes:
        n, k, f = _print_apple_outcome(outcome)
        total_new += n
        total_known += k
        total_failed += f
    for outcome in result.ytmusic_outcomes:
        n, k, f = _print_ytmusic_outcome(outcome)
        total_new += n
        total_known += k
        total_failed += f

    total_downloaded = total_present = total_ep_failed = 0
    for outcome in result.podcast_outcomes:
        d, p, f = _print_podcast_outcome(outcome)
        total_downloaded += d
        total_present += p
        total_ep_failed += f

    for name in result.unmatched_playlists:
        print(f"WARNING: no playlist matched --playlist {name!r}")
    for name in result.unmatched_shows:
        print(f"WARNING: no subscription matched --show {name!r}")
    for error in result.source_errors:
        print(f"ERROR: {error}")

    print(
        f"Total music: {total_new} new, {total_known} already known, "
        f"{total_failed} failed"
    )
    print(
        f"Total podcasts: {total_downloaded} downloaded, {total_present} already present, "
        f"{total_ep_failed} episode(s) failed"
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="music-stack")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync", help="Sync every configured playlist/show for a profile in one call"
    )
    sync_parser.add_argument("--profile", required=True, help="Path to profile YAML")
    sync_parser.add_argument(
        "--global-config", default="config/global.yaml", help="Path to global.yaml"
    )
    sync_parser.add_argument(
        "--config-root",
        default=None,
        help="Real host root that global.yaml's /config/... paths resolve "
        "under. Defaults to --global-config's own parent directory.",
    )
    sync_parser.add_argument(
        "--library-root",
        default=None,
        help="Real host root containing music/, playlists/, podcasts/. "
        "Defaults to a 'library' directory next to --config-root.",
    )
    sync_parser.add_argument(
        "--state-root",
        default=None,
        help="Real host root for per-profile state dbs. Defaults to a "
        "'state' directory next to --config-root.",
    )
    sync_parser.add_argument(
        "--source",
        action="append",
        choices=(*SUPPORTED_SOURCES, *UNSUPPORTED_SOURCES),
        help="Restrict sync to this source (repeatable). Defaults to "
        f"all of: {', '.join(SUPPORTED_SOURCES)}.",
    )
    sync_parser.add_argument(
        "--playlist",
        action="append",
        help="Restrict apple_music/ytmusic sync to this playlist name (repeatable).",
    )
    sync_parser.add_argument(
        "--show",
        action="append",
        help="Restrict podcast sync to this show, by UUID or title "
        "(case-insensitive, repeatable).",
    )
    sync_parser.add_argument("--storefront", default="us")
    sync_parser.add_argument("--lock-timeout", type=float, default=1800)
    sync_parser.set_defaults(func=_cmd_sync)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
