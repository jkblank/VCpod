from __future__ import annotations

from typing import Annotated, Literal

from croniter import croniter
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _validate_cron_expression(value: str) -> str:
    if not croniter.is_valid(value):
        raise ValueError(f"invalid cron expression: {value!r}")
    return value


# Shared by every fetch_schedule/schedule field below (PlaylistEntry,
# ShowOverride, ProfilePodcastsConfig, FetchSettings) so cron-string
# validation is defined once rather than as four near-identical
# @field_validators. See notes.md / M9 for the scheduled-fetch design.
CronSchedule = Annotated[str, AfterValidator(_validate_cron_expression)]


class Paths(StrictModel):
    library_root: str
    state_root: str


class AppleMusicSource(StrictModel):
    enabled: bool
    cookies_file: str


class SpotifySource(StrictModel):
    enabled: bool
    credentials_file: str


class YtMusicSource(StrictModel):
    enabled: bool
    # ytmusicapi OAuth token file — only needed for authenticated calls
    # (list_playlists, i.e. the account's own library). fetch_playlist
    # against a public playlist works fine without it.
    oauth_file: str
    # yt-dlp's YouTube cookies (Netscape format), separate from
    # oauth_file because it authenticates a different thing: the actual
    # CDN download, which YouTube's bot-check gates independently of
    # ytmusicapi's own session. See notes.md.
    cookies_file: str


class SourcesConfig(StrictModel):
    apple_music: AppleMusicSource
    spotify: SpotifySource
    ytmusic: YtMusicSource


class PocketCastsGlobalConfig(StrictModel):
    poll_interval_minutes: int = Field(gt=0)


class PodcastsGlobalConfig(StrictModel):
    pocketcasts: PocketCastsGlobalConfig


class GlobalConfig(StrictModel):
    paths: Paths
    sources: SourcesConfig
    podcasts: PodcastsGlobalConfig


class DeviceMatch(StrictModel):
    match_by: Literal["serial", "volume_label"]
    match_value: str


class PlaylistEntry(StrictModel):
    name: str
    source: Literal["apple_music", "spotify", "ytmusic"]
    source_id: str
    # "absolute" (default): the local .m3u8 always mirrors the source
    # playlist's current contents exactly, including removals.
    # "additive": tracks are only ever added locally, never removed, even
    # if the source playlist no longer has them — for platform-curated
    # playlists (e.g. Apple Music's algorithmic Mixes) that rotate/shrink
    # their contents to stay a fixed length. See notes.md.
    sync_mode: Literal["absolute", "additive"] = "absolute"
    # Overrides fetch.schedule (see FetchSettings/ProfileConfig) for this
    # playlist only. None = fall back to the profile-level default.
    fetch_schedule: CronSchedule | None = None


class ExternalLibraryConfig(StrictModel):
    path: str
    # "include" (default): only files matching a `selections` entry are
    # synced — a whitelist ("only include Linkin Park").
    # "exclude": every file is synced EXCEPT those matching a
    # `selections` entry — a blacklist ("my whole library, but exclude
    # Alanis Morissette"). Empty `selections` + exclude = sync
    # everything (today's wholesale behavior); empty `selections` +
    # include = sync nothing. See notes.md.
    mode: Literal["include", "exclude"] = "include"
    # Relative path fragments under `path`, matched by prefix against
    # each file's path relative to `path`:
    #   "Artist"                  -> every album/track by that artist
    #   "Artist/Album"            -> every track on that album
    #   "Artist/Album/Track.m4a"  -> a single track
    # An entry may also be a single-key mapping of artist -> list of
    # album/track names relative to that artist, as shorthand for
    # several entries that all start with the same "Artist/" prefix:
    #   "Talking Heads":
    #     - "Performance"
    #     - "Remixed"
    # is exactly equivalent to ["Talking Heads/Performance",
    # "Talking Heads/Remixed"] — flattened below before storage, so
    # everything downstream only ever deals with plain strings.
    selections: list[str] = Field(default_factory=list)

    @field_validator("selections", mode="before")
    @classmethod
    def _flatten_nested_selections(cls, value: object) -> object:
        if not isinstance(value, list):
            return value
        flattened: list[str] = []
        for item in value:
            if isinstance(item, str):
                flattened.append(item)
            elif isinstance(item, dict):
                for artist, children in item.items():
                    if not isinstance(artist, str) or not isinstance(children, list):
                        raise ValueError(
                            f"invalid selections entry: {item!r} — expected "
                            "'Artist': [\"Album\", ...]"
                        )
                    for child in children:
                        if not isinstance(child, str):
                            raise ValueError(
                                f"invalid selections entry under {artist!r}: {child!r}"
                            )
                        flattened.append(f"{artist}/{child}")
            else:
                raise ValueError(f"invalid selections entry: {item!r}")
        return flattened


class ProfilePocketCastsConfig(StrictModel):
    credentials_file: str


class ShowOverride(StrictModel):
    """A `podcasts.shows` list entry that carries a per-show fetch_schedule
    override, e.g. `- "Weekly Deep Dive": {fetch_schedule: "0 6 * * 1"}` in
    YAML. `name` matches the same show-identifier convention `shows`
    already uses elsewhere (Pocket Casts UUID or case-insensitive title —
    see resolve_show_selection)."""

    name: str
    fetch_schedule: CronSchedule | None = None


class ProfilePodcastsConfig(StrictModel):
    pocketcasts: ProfilePocketCastsConfig
    sync_unplayed_only: bool
    max_episodes_per_show: int = Field(gt=0)
    shows: Literal["all"] | list[str | ShowOverride] = "all"
    # Overrides fetch.schedule for every show below, unless a show has its
    # own ShowOverride.fetch_schedule (which wins over this). None = fall
    # back to the profile-level default.
    fetch_schedule: CronSchedule | None = None

    @field_validator("shows", mode="before")
    @classmethod
    def _normalize_shows(cls, value: object) -> object:
        """Unlike ExternalLibraryConfig's `_flatten_nested_selections`
        (which collapses a mapping entry down to a plain string), a shows
        entry's mapping needs to survive as a structured ShowOverride so
        its fetch_schedule isn't lost — so this normalizes shape rather
        than flattening it away."""
        if value == "all" or not isinstance(value, list):
            return value
        normalized: list[object] = []
        for item in value:
            if isinstance(item, (str, ShowOverride)):
                normalized.append(item)
            elif isinstance(item, dict) and len(item) == 1:
                (name, overrides), = item.items()
                if not isinstance(name, str):
                    raise ValueError(f"invalid shows entry: {item!r}")
                overrides = overrides or {}
                if not isinstance(overrides, dict):
                    raise ValueError(
                        f"invalid shows entry {name!r}: expected a mapping of "
                        f"overrides (e.g. fetch_schedule), got {overrides!r}"
                    )
                normalized.append({"name": name, **overrides})
            else:
                raise ValueError(f"invalid shows entry: {item!r}")
        return normalized

    @property
    def show_names(self) -> Literal["all"] | list[str]:
        """Plain show identifiers, stripped of any fetch_schedule
        override — for callers (e.g. run_sync/resolve_show_selection) that
        only care about which shows are selected, not their schedules."""
        if self.shows == "all":
            return "all"
        return [s if isinstance(s, str) else s.name for s in self.shows]

    # "played" (default): an episode counts as done once played_up_to
    # indicates playback, per Pocket Casts' own playingStatus (merged with
    # local device read-back — see sync_podcast). "archived": use Pocket
    # Casts' Archive feature instead (their API field is confusingly named
    # isDeleted) — a distinct, user-driven signal that doesn't always match
    # played status (an episode can be played but not archived, or archived
    # without ever being played), and better reflects "I'm done with this"
    # for accounts that use Archive deliberately. See notes.md.
    episode_filter: Literal["played", "archived"] = "played"
    # Per-show episode selection order, keyed by Pocket Casts podcast
    # UUID (same convention `shows` already uses). Not listed = "newest":
    # sort newest-first, take the top max_episodes_per_show. "next":
    # sort oldest-first among unplayed episodes instead, for shows meant
    # to be listened to in chronological order (serialized fiction,
    # courses) rather than "whatever's newest." See notes.md.
    fill_modes: dict[str, Literal["newest", "next"]] = Field(default_factory=dict)


class SyncSettings(StrictModel):
    trigger: Literal["on_connect", "manual", "cron"]
    transcode_format: str
    push_play_status_back: bool


class FetchSettings(StrictModel):
    # Profile-level default fetch schedule (cron expression), used by any
    # playlist/show below that doesn't set its own fetch_schedule. None =
    # no default — a playlist/show with no schedule anywhere in the
    # precedence chain is manual-only, never picked up by fetch-scheduler.
    schedule: CronSchedule | None = None


class ProfileConfig(StrictModel):
    profile: str
    device: DeviceMatch
    playlists: list[PlaylistEntry]
    podcasts: ProfilePodcastsConfig
    sync: SyncSettings
    external_library: ExternalLibraryConfig | None = None
    fetch: FetchSettings = Field(default_factory=FetchSettings)
