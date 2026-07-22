from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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


class ProfilePodcastsConfig(StrictModel):
    pocketcasts: ProfilePocketCastsConfig
    sync_unplayed_only: bool
    max_episodes_per_show: int = Field(gt=0)
    shows: Literal["all"] | list[str] = "all"
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


class ProfileConfig(StrictModel):
    profile: str
    device: DeviceMatch
    playlists: list[PlaylistEntry]
    podcasts: ProfilePodcastsConfig
    sync: SyncSettings
    external_library: ExternalLibraryConfig | None = None
