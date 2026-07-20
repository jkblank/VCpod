from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    oauth_file: str


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


class ProfilePocketCastsConfig(StrictModel):
    credentials_file: str


class ProfilePodcastsConfig(StrictModel):
    pocketcasts: ProfilePocketCastsConfig
    sync_unplayed_only: bool
    max_episodes_per_show: int = Field(gt=0)
    shows: Literal["all"] | list[str] = "all"
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
