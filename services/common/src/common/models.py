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
    # JSON {"client_id", "client_secret"} for the Google OAuth client
    # oauth_file's token was minted with. ytmusicapi has no default
    # client of its own -- every user creates their own via Google
    # Cloud Console -- and the *same* client_id/secret must be supplied
    # again on every use for token refresh to work, not just at capture
    # time (a bare oauth_file with no matching client raises
    # YTMusicUserError the moment the token expires). Optional: absent
    # (empty string) means oauth_file, if present, will work until it
    # expires and then need re-capturing instead of auto-refreshing.
    oauth_client_file: str = ""


class SourcesConfig(StrictModel):
    apple_music: AppleMusicSource
    spotify: SpotifySource
    ytmusic: YtMusicSource


class PocketCastsGlobalConfig(StrictModel):
    poll_interval_minutes: int = Field(gt=0)


class PodcastsGlobalConfig(StrictModel):
    pocketcasts: PocketCastsGlobalConfig


class LibraryManagerConfig(StrictModel):
    # Global (not per-profile): dedup/cleanup operate on the whole shared
    # library, same as library-manager's own CLI (--state-dir globs every
    # profile's *.sqlite at once so one pass updates them all). No
    # schedule of their own — runs as a post-step whenever any profile
    # actually fetches this tick (see fetch_scheduler.loop), gated by
    # these enable flags rather than a separate cron. False = manual-only
    # (unchanged default behavior, via the `library-manager` CLI).
    dedup_enabled: bool = False
    cleanup_enabled: bool = False
    # Re-encodes embedded cover art through Pillow for any track whose
    # art carries a marker Pillow's own encoder never writes (DRI,
    # APP13/Photoshop, APP14/Adobe, progressive) — found live to break
    # on-device album art rendering for at least one real track,
    # independent of library size. See library_manager.artwork and
    # notes.md, 2026-08-25.
    normalize_artwork_enabled: bool = False
    # Mirror find_duplicate_groups'/sweep_quarantine's own defaults —
    # present here so these can be overridden via config (e.g. a GUI)
    # without a code change.
    fuzzy_threshold: float = Field(default=92.0, gt=0, le=100)
    quarantine_older_than_days: int = Field(default=14, gt=0)


class BackupMaintenanceConfig(StrictModel):
    # No schedule of its own, same reasoning as LibraryManagerConfig
    # above — runs as a post-step whenever any profile fetches this
    # tick. False = manual-only.
    prune_enabled: bool = False
    # Applied to any device_backups/{device_id} directory not resolved to
    # a profile (see common.backups.resolve_retention_map).
    default_keep_last: int = Field(default=3, gt=0)
    default_max_age_days: int = Field(default=14, gt=0)


class AudiobookManagerConfig(StrictModel):
    # Where raw, not-yet-processed audiobook source folders land (e.g.
    # Libby DevTools-captured MP3 parts, one subfolder per book — see
    # services/audiobook-manager/README.md's manual-acquisition
    # workflow). Global, not per-profile: library/audiobooks itself is
    # one shared pool synced from (same reasoning as library/music), so
    # "where do raw captures sit before processing" isn't a per-profile
    # question either. A real host path, used directly like
    # ExternalLibraryConfig.path -- never /config/...-container-style.
    # Optional: empty means discover has nowhere configured to scan yet.
    discover_root: str = ""


class GlobalConfig(StrictModel):
    paths: Paths
    sources: SourcesConfig
    podcasts: PodcastsGlobalConfig
    library_manager: LibraryManagerConfig = Field(default_factory=LibraryManagerConfig)
    backups: BackupMaintenanceConfig = Field(default_factory=BackupMaintenanceConfig)
    audiobook_manager: AudiobookManagerConfig = Field(default_factory=AudiobookManagerConfig)


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


def _flatten_nested_selection_entries(value: object) -> object:
    """Shared by ExternalLibraryConfig.selections and
    AudiobooksConfig.selections — flattens a mapping-shorthand entry
    (e.g. "Artist": ["Album1", "Album2"]) into plain "Artist/Album1",
    "Artist/Album2" strings, so everything downstream (sync_orchestrator's
    selection-resolution code) only ever deals with plain path-fragment
    strings, regardless of which config section they came from."""
    if not isinstance(value, list):
        return value
    flattened: list[str] = []
    for item in value:
        if isinstance(item, str):
            flattened.append(item)
        elif isinstance(item, dict):
            for parent, children in item.items():
                if not isinstance(parent, str) or not isinstance(children, list):
                    raise ValueError(
                        f"invalid selections entry: {item!r} — expected "
                        "'Name': [\"Child\", ...]"
                    )
                for child in children:
                    if not isinstance(child, str):
                        raise ValueError(
                            f"invalid selections entry under {parent!r}: {child!r}"
                        )
                    flattened.append(f"{parent}/{child}")
        else:
            raise ValueError(f"invalid selections entry: {item!r}")
    return flattened


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
        return _flatten_nested_selection_entries(value)


class AudiobooksConfig(StrictModel):
    # "include" (default): only books matching a `selections` entry are
    # synced — a whitelist. Empty `selections` + include = sync every
    # audiobook — deliberately the opposite default from
    # ExternalLibraryConfig's "empty + include = sync nothing": most
    # profiles don't need per-book curation, so audiobooks default to
    # "just sync everything" (same behavior as `audiobooks` being left
    # unset entirely). "exclude": every audiobook is synced EXCEPT those
    # matching a `selections` entry.
    mode: Literal["include", "exclude"] = "include"
    # Relative path fragments under library_root/audiobooks, matched by
    # prefix — same convention as ExternalLibraryConfig.selections.
    # beets-audible's own layout is {Author}/{Album}/{Title}.m4b:
    #   "Franz Kafka"             -> every book by that author
    #   "Franz Kafka/The Trial"   -> one specific book
    # A mapping entry (author -> list of titles) is flattened the same
    # way as ExternalLibraryConfig.selections.
    selections: list[str] = Field(default_factory=list)

    @field_validator("selections", mode="before")
    @classmethod
    def _flatten_nested_selections(cls, value: object) -> object:
        return _flatten_nested_selection_entries(value)


class MusicLibraryConfig(StrictModel):
    """Scopes library_root/music's contribution to this profile's device
    *general* library — separate from playlist tracks, which are always
    included regardless of this setting. On a real iPod, playlists and
    the on-device "Songs" list share one flat track table (iTunesDB has
    no such thing as a playlist-only track); rockbox_sync.py's own
    playlist-track handling independently guarantees the same for
    Rockbox mode. So this can only ever narrow the *extra* tracks beyond
    a profile's own playlists, never exclude a playlist's own tracks.

    profile.music left unset (None, the default) means every profile
    behaves exactly as before this option existed: the whole shared pool
    syncs to every device. See notes.md / [[feedback-shared-library-pool-not-a-bug]]
    for why that's the deliberate default, not a bug — this field is the
    opt-in for a profile (e.g. a device meant to carry only specific
    playlists) that wants something narrower.
    """

    # "include" (default): only files matching a `selections` entry are
    # synced beyond playlist tracks — a whitelist. Empty `selections` +
    # include = sync nothing extra beyond playlist tracks (same
    # convention as ExternalLibraryConfig, not AudiobooksConfig — a
    # profile that sets `music:` at all is opting into curation, so
    # "curate down to nothing" is the sensible empty default here).
    # "exclude": every track in library/music is synced EXCEPT those
    # matching a `selections` entry.
    mode: Literal["include", "exclude"] = "include"
    # Relative path fragments under library_root/music, matched by
    # prefix — same convention as ExternalLibraryConfig.selections:
    #   "Artist"                  -> every album/track by that artist
    #   "Artist/Album"            -> every track on that album
    #   "Artist/Album/Track.m4a"  -> a single track
    selections: list[str] = Field(default_factory=list)

    @field_validator("selections", mode="before")
    @classmethod
    def _flatten_nested_selections(cls, value: object) -> object:
        return _flatten_nested_selection_entries(value)


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
        override — for callers (e.g. run_fetch/resolve_show_selection) that
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
    # Once an episode is played (remotely via Pocket Casts, or locally via
    # sync-orchestrator's device read-back), delete its downloaded audio
    # file so it stops taking up disk space and drops out of the next
    # device sync's podcast plan. Only takes effect when sync_unplayed_only
    # is also True — sync_unplayed_only=False means the profile deliberately
    # wants played episodes downloaded/kept too (e.g. an archive), and
    # deleting them the instant they're downloaded would fight that intent.
    delete_played_episodes: bool = True


class SyncSettings(StrictModel):
    trigger: Literal["on_connect", "manual", "cron"]
    transcode_format: str
    push_play_status_back: bool
    # "itunes" (default): write iTunesDB/ArtworkDB via sync_orchestrator.sync
    # (iopenpod's SyncEngine), same as every profile before this field
    # existed. "rockbox": a plain filesystem mirror instead — no iTunesDB/
    # ArtworkDB at all — via sync_orchestrator.rockbox_sync, for a device
    # running Rockbox firmware (which reads file tags, not iTunesDB). See
    # notes.md for why this is a separate code path rather than iopenpod's
    # own rockbox_metadata_support bolt-on.
    mode: Literal["itunes", "rockbox"] = "itunes"


class FetchSettings(StrictModel):
    # Profile-level default fetch schedule (cron expression), used by any
    # playlist/show below that doesn't set its own fetch_schedule. None =
    # no default — a playlist/show with no schedule anywhere in the
    # precedence chain is manual-only, never picked up by fetch-scheduler.
    schedule: CronSchedule | None = None


class ProfileBackupRetention(StrictModel):
    # None on either field = "use GlobalConfig.backups.default_*" — a
    # per-field override, not all-or-nothing.
    keep_last: int | None = Field(default=None, gt=0)
    max_age_days: int | None = Field(default=None, gt=0)


class ProfileConfig(StrictModel):
    profile: str
    device: DeviceMatch
    playlists: list[PlaylistEntry]
    podcasts: ProfilePodcastsConfig
    sync: SyncSettings
    external_library: ExternalLibraryConfig | None = None
    audiobooks: AudiobooksConfig | None = None
    music: MusicLibraryConfig | None = None
    fetch: FetchSettings = Field(default_factory=FetchSettings)
    backups: ProfileBackupRetention | None = None
