from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from croniter import croniter

from common.models import ProfileConfig, ShowOverride


def next_fetch_time(
    schedule: str | None, last_fetched_at: datetime | None, now: datetime
) -> datetime | None:
    """schedule=None -> None (no schedule anywhere in the precedence
    chain, manual-only). Never-fetched + a schedule -> now (due
    immediately, first run). Otherwise croniter's next fire time computed
    from last_fetched_at."""
    if schedule is None:
        return None
    if last_fetched_at is None:
        return now
    return croniter(schedule, last_fetched_at).get_next(datetime)


def is_due(schedule: str | None, last_fetched_at: datetime | None, now: datetime) -> bool:
    """Is this target's next scheduled fetch due right now? Used by
    fetch-scheduler's tick (the horizon=0 case of is_due_within)."""
    nxt = next_fetch_time(schedule, last_fetched_at, now)
    return nxt is not None and nxt <= now


def is_due_within(
    schedule: str | None,
    last_fetched_at: datetime | None,
    now: datetime,
    horizon: timedelta,
) -> bool:
    """Is this target's next scheduled fetch due within `horizon` of now?
    Used by auto-sync's conditional pre-fetch decision (a 4h horizon by
    default) — is_due is exactly this with horizon=timedelta(0)."""
    nxt = next_fetch_time(schedule, last_fetched_at, now)
    return nxt is not None and nxt <= now + horizon


@dataclass(frozen=True)
class FetchTarget:
    target_type: Literal["playlist", "podcast_show"]
    target_id: str
    source: str | None  # playlist source (apple_music/spotify/ytmusic); None for podcast_show
    schedule: str | None  # fully resolved — precedence already applied


def iter_fetch_targets(profile: ProfileConfig) -> list[FetchTarget]:
    """Enumerate every schedulable unit in a profile with its resolved
    fetch schedule (most-specific-wins: per-playlist/per-show > podcasts-
    level > profile default).

    podcasts.shows == "all" collapses to a single synthetic "__all__"
    target rather than one target per subscription — enumerating real
    subscriptions would require a live Pocket Casts API call just to
    build the due-check, defeating the point of a cheap scheduler tick.
    """
    targets = [
        FetchTarget(
            target_type="playlist",
            target_id=playlist.name,
            source=playlist.source,
            schedule=playlist.fetch_schedule or profile.fetch.schedule,
        )
        for playlist in profile.playlists
    ]

    podcasts = profile.podcasts
    if podcasts.shows == "all":
        targets.append(
            FetchTarget(
                target_type="podcast_show",
                target_id="__all__",
                source=None,
                schedule=podcasts.fetch_schedule or profile.fetch.schedule,
            )
        )
    else:
        for show in podcasts.shows:
            if isinstance(show, ShowOverride):
                name, override_schedule = show.name, show.fetch_schedule
            else:
                name, override_schedule = show, None
            targets.append(
                FetchTarget(
                    target_type="podcast_show",
                    target_id=name,
                    source=None,
                    schedule=(
                        override_schedule
                        or podcasts.fetch_schedule
                        or profile.fetch.schedule
                    ),
                )
            )
    return targets


@dataclass(frozen=True)
class ResolvedFetchScope:
    sources: set[str]
    playlist_names: list[str] | None  # None = no playlist targets in scope
    show_names: list[str] | None  # None = no show targets, OR the "__all__"
    # sentinel (fetch every subscription) — both mean "don't pass a show
    # filter downstream" (run_fetch/`music-stack fetch --show` treat "no
    # filter" as "use the profile's own podcasts.shows setting").


def resolve_fetch_scope(targets: list[FetchTarget]) -> ResolvedFetchScope:
    """Turn a list of (already due-filtered) FetchTargets into the
    sources/playlist_names/show_names shape both run_fetch (in-process,
    fetch-scheduler) and `music-stack fetch` CLI flags (subprocess,
    auto-sync's pre-fetch) need — a single shared implementation so this
    logic (and the empty-list-vs-None distinction it has to get right)
    isn't duplicated in both consumers."""
    sources: set[str] = set()
    playlist_names: list[str] = []
    show_ids: list[str] = []
    saw_all_sentinel = False

    for target in targets:
        if target.target_type == "playlist":
            sources.add(target.source)
            playlist_names.append(target.target_id)
        else:
            sources.add("podcasts")
            if target.target_id == "__all__":
                saw_all_sentinel = True
            else:
                show_ids.append(target.target_id)

    show_names = None if (saw_all_sentinel or not show_ids) else show_ids
    return ResolvedFetchScope(
        sources=sources,
        playlist_names=playlist_names or None,
        show_names=show_names,
    )
