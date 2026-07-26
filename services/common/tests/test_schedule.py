from datetime import datetime, timedelta, timezone

from common.models import (
    DeviceMatch,
    PlaylistEntry,
    ProfileConfig,
    ProfilePocketCastsConfig,
    ProfilePodcastsConfig,
    ShowOverride,
    SyncSettings,
)
from common.schedule import (
    FetchTarget,
    ResolvedFetchScope,
    is_due,
    is_due_within,
    iter_fetch_targets,
    next_fetch_time,
    resolve_fetch_scope,
)

NOW = datetime(2026, 7, 25, 12, 17, tzinfo=timezone.utc)
HOURLY = "0 * * * *"
DAILY_AT_3AM = "0 3 * * *"


def test_next_fetch_time_no_schedule_is_none():
    assert next_fetch_time(None, None, NOW) is None
    assert next_fetch_time(None, NOW - timedelta(hours=1), NOW) is None


def test_next_fetch_time_never_fetched_is_due_now():
    assert next_fetch_time(HOURLY, None, NOW) == NOW


def test_next_fetch_time_computed_from_last_fetched_at():
    last = datetime(2026, 7, 25, 11, 47, tzinfo=timezone.utc)
    assert next_fetch_time(HOURLY, last, NOW) == datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def test_is_due_true_when_never_fetched():
    assert is_due(HOURLY, None, NOW) is True


def test_is_due_false_just_fetched_hourly_schedule():
    # Fetched a minute ago (12:16); hourly cron's next fire from there is
    # 13:00, still in the future relative to NOW (12:17) — not due yet.
    last = NOW - timedelta(minutes=1)
    assert is_due(HOURLY, last, NOW) is False


def test_is_due_true_fetched_two_hours_ago_hourly_schedule():
    # Fetched at 10:17; hourly cron's next fire from there is 11:00,
    # already in the past relative to NOW (12:17) — due.
    last = NOW - timedelta(hours=2)
    assert is_due(HOURLY, last, NOW) is True


def test_is_due_false_when_schedule_is_none():
    assert is_due(None, None, NOW) is False
    assert is_due(None, NOW - timedelta(days=1), NOW) is False


def test_is_due_within_true_when_next_fetch_inside_horizon():
    # now=23:30 on the 25th; last fetch was 04:00 the same day (right
    # after that day's 3am run), so the *next* 3am fire is 03:00 on the
    # 26th — 3.5h away, inside a 4h horizon.
    now = datetime(2026, 7, 25, 23, 30, tzinfo=timezone.utc)
    last = datetime(2026, 7, 25, 4, 0, tzinfo=timezone.utc)
    assert is_due_within(DAILY_AT_3AM, last, now, timedelta(hours=4)) is True


def test_is_due_within_false_when_next_fetch_outside_horizon():
    # now=18:00 on the 25th, same last-fetch as above — next 3am fire
    # (03:00 on the 26th) is 9h away, outside a 4h horizon.
    now = datetime(2026, 7, 25, 18, 0, tzinfo=timezone.utc)
    last = datetime(2026, 7, 25, 4, 0, tzinfo=timezone.utc)
    assert is_due_within(DAILY_AT_3AM, last, now, timedelta(hours=4)) is False


def test_is_due_within_horizon_zero_matches_is_due():
    last = NOW - timedelta(hours=2)
    assert is_due_within(HOURLY, last, NOW, timedelta(0)) == is_due(HOURLY, last, NOW)


def test_is_due_within_false_when_schedule_is_none():
    assert is_due_within(None, None, NOW, timedelta(hours=4)) is False


def _profile(**overrides) -> ProfileConfig:
    base = dict(
        profile="john",
        device=DeviceMatch(match_by="volume_label", match_value="TEST"),
        playlists=[],
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
        ),
        sync=SyncSettings(trigger="manual", transcode_format="alac", push_play_status_back=False),
    )
    base.update(overrides)
    return ProfileConfig(**base)


def test_iter_fetch_targets_playlist_uses_own_override_over_profile_default():
    profile = _profile(
        fetch={"schedule": "0 3 * * *"},
        playlists=[
            PlaylistEntry(name="Chill", source="spotify", source_id="1", fetch_schedule="0 */6 * * *"),
            PlaylistEntry(name="Elevate", source="apple_music", source_id="2"),
        ],
    )
    targets = {t.target_id: t for t in iter_fetch_targets(profile) if t.target_type == "playlist"}

    assert targets["Chill"].schedule == "0 */6 * * *"
    assert targets["Chill"].source == "spotify"
    assert targets["Elevate"].schedule == "0 3 * * *"  # falls back to profile default


def test_iter_fetch_targets_podcasts_level_overrides_profile_default():
    profile = _profile(
        fetch={"schedule": "0 3 * * *"},
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
            fetch_schedule="0 * * * *",
            shows=["Daily News"],
        ),
    )
    targets = [t for t in iter_fetch_targets(profile) if t.target_type == "podcast_show"]

    assert len(targets) == 1
    assert targets[0].target_id == "Daily News"
    assert targets[0].schedule == "0 * * * *"


def test_iter_fetch_targets_per_show_override_wins_over_podcasts_level():
    profile = _profile(
        fetch={"schedule": "0 3 * * *"},
        podcasts=ProfilePodcastsConfig(
            pocketcasts=ProfilePocketCastsConfig(credentials_file="creds.json"),
            sync_unplayed_only=True,
            max_episodes_per_show=5,
            fetch_schedule="0 * * * *",
            shows=["Daily News", ShowOverride(name="Weekly Deep Dive", fetch_schedule="0 6 * * 1")],
        ),
    )
    targets = {
        t.target_id: t for t in iter_fetch_targets(profile) if t.target_type == "podcast_show"
    }

    assert targets["Daily News"].schedule == "0 * * * *"  # podcasts-level
    assert targets["Weekly Deep Dive"].schedule == "0 6 * * 1"  # per-show override


def test_iter_fetch_targets_shows_all_collapses_to_one_sentinel_target():
    profile = _profile(fetch={"schedule": "0 3 * * *"})  # podcasts.shows defaults to "all"
    targets = [t for t in iter_fetch_targets(profile) if t.target_type == "podcast_show"]

    assert len(targets) == 1
    assert targets[0].target_id == "__all__"
    assert targets[0].schedule == "0 3 * * *"


def test_iter_fetch_targets_no_schedule_anywhere_is_none():
    profile = _profile(playlists=[PlaylistEntry(name="Chill", source="spotify", source_id="1")])
    targets = {t.target_id: t for t in iter_fetch_targets(profile)}

    assert targets["Chill"].schedule is None
    assert targets["__all__"].schedule is None


def test_resolve_fetch_scope_builds_sources_and_names():
    targets = [
        FetchTarget(target_type="playlist", target_id="Chill", source="spotify", schedule=HOURLY),
        FetchTarget(target_type="playlist", target_id="Elevate", source="apple_music", schedule=HOURLY),
        FetchTarget(target_type="podcast_show", target_id="Daily News", source=None, schedule=HOURLY),
    ]

    scope = resolve_fetch_scope(targets)

    assert scope.sources == {"spotify", "apple_music", "podcasts"}
    assert sorted(scope.playlist_names) == ["Chill", "Elevate"]
    assert scope.show_names == ["Daily News"]


def test_resolve_fetch_scope_all_sentinel_produces_none_show_names():
    targets = [FetchTarget(target_type="podcast_show", target_id="__all__", source=None, schedule=HOURLY)]

    scope = resolve_fetch_scope(targets)

    assert scope.sources == {"podcasts"}
    assert scope.playlist_names is None
    assert scope.show_names is None


def test_resolve_fetch_scope_empty_targets_is_all_none():
    scope = resolve_fetch_scope([])
    assert scope == ResolvedFetchScope(sources=set(), playlist_names=None, show_names=None)
