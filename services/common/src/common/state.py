from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class TrackRecord:
    source: str
    source_id: str
    local_path: str
    title: str
    artist: str
    downloaded_at: str


@dataclass
class EpisodeRecord:
    episode_uuid: str
    podcast_uuid: str
    show_name: str
    local_path: str
    played: bool
    played_up_to: int
    downloaded_at: str
    title: str = ""
    audio_url: str = ""
    duration_seconds: int = 0
    # Set by sync-orchestrator when device read-back finds a played-state
    # change; cleared by podcast-manager once successfully pushed to
    # Pocket Casts. See notes.md's M8 write-up.
    pending_push: bool = False
    # Set by podcast_manager.download.prune_unsubscribed_shows once the
    # show is no longer in the account's Pocket Casts subscriptions.
    # Per-profile (unlike the shared local file) — see notes.md.
    unsubscribed: bool = False
    # RSS-sourced metadata (podcast_manager/rss.py) -- Pocket Casts' own
    # API doesn't expose any of these. Best-effort: blank/None when the
    # show's feed couldn't be resolved or doesn't tag them (not every
    # podcast sets itunes:episode/itunes:season). See notes.md.
    description: str = ""
    episode_number: int | None = None
    season_number: int | None = None
    published_at: str = ""


class StateDB:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracks (
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                local_path TEXT NOT NULL,
                title TEXT NOT NULL,
                artist TEXT NOT NULL,
                downloaded_at TEXT NOT NULL,
                PRIMARY KEY (source, source_id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS episodes (
                episode_uuid TEXT NOT NULL,
                podcast_uuid TEXT NOT NULL,
                show_name TEXT NOT NULL,
                local_path TEXT NOT NULL,
                played INTEGER NOT NULL,
                played_up_to INTEGER NOT NULL,
                downloaded_at TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                audio_url TEXT NOT NULL DEFAULT '',
                duration_seconds INTEGER NOT NULL DEFAULT 0,
                pending_push INTEGER NOT NULL DEFAULT 0,
                unsubscribed INTEGER NOT NULL DEFAULT 0,
                description TEXT NOT NULL DEFAULT '',
                episode_number INTEGER,
                season_number INTEGER,
                published_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (episode_uuid)
            )
            """
        )
        self._migrate_episodes_columns()
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fetch_runs (
                target_type TEXT NOT NULL,
                target_id TEXT NOT NULL,
                last_fetched_at TEXT NOT NULL,
                PRIMARY KEY (target_type, target_id)
            )
            """
        )
        self._conn.commit()

    def _migrate_episodes_columns(self) -> None:
        # Upgrades a pre-existing episodes table (created before title/
        # audio_url/duration_seconds/pending_push existed) in place. CREATE
        # TABLE IF NOT EXISTS above is a no-op against an already-existing
        # table, so older DBs need these added explicitly.
        existing = {row[1] for row in self._conn.execute("PRAGMA table_info(episodes)")}
        for column, ddl in (
            ("title", "TEXT NOT NULL DEFAULT ''"),
            ("audio_url", "TEXT NOT NULL DEFAULT ''"),
            ("duration_seconds", "INTEGER NOT NULL DEFAULT 0"),
            ("pending_push", "INTEGER NOT NULL DEFAULT 0"),
            ("unsubscribed", "INTEGER NOT NULL DEFAULT 0"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
            ("episode_number", "INTEGER"),
            ("season_number", "INTEGER"),
            ("published_at", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                self._conn.execute(f"ALTER TABLE episodes ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateDB":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_track(self, source: str, source_id: str) -> TrackRecord | None:
        row = self._conn.execute(
            "SELECT source, source_id, local_path, title, artist, downloaded_at "
            "FROM tracks WHERE source = ? AND source_id = ?",
            (source, source_id),
        ).fetchone()
        return TrackRecord(*row) if row else None

    def update_local_path(self, source: str, source_id: str, new_path: str) -> bool:
        """Repoints an existing row's local_path (e.g. after dedup collapses
        it onto a canonical file). Returns True if a row was updated, False
        if no row for (source, source_id) exists in this db."""
        cursor = self._conn.execute(
            "UPDATE tracks SET local_path = ? WHERE source = ? AND source_id = ?",
            (new_path, source, source_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def record_track(self, record: TrackRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO tracks (source, source_id, local_path, title, artist, downloaded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, source_id) DO UPDATE SET
                local_path = excluded.local_path,
                title = excluded.title,
                artist = excluded.artist,
                downloaded_at = excluded.downloaded_at
            """,
            (
                record.source,
                record.source_id,
                record.local_path,
                record.title,
                record.artist,
                record.downloaded_at,
            ),
        )
        self._conn.commit()

    def count_tracks(self) -> int:
        """How many tracks this profile's state db believes are on the
        device -- the same "believed synced" bookkeeping the sync
        pipeline itself already relies on, not a fresh device scan.
        Used by the web GUI's Overview dashboard for a real per-profile
        track count."""
        (count,) = self._conn.execute("SELECT COUNT(*) FROM tracks").fetchone()
        return count

    _EPISODE_COLUMNS = (
        "episode_uuid, podcast_uuid, show_name, local_path, played, "
        "played_up_to, downloaded_at, title, audio_url, duration_seconds, "
        "pending_push, unsubscribed, description, episode_number, "
        "season_number, published_at"
    )

    @staticmethod
    def _episode_from_row(row: tuple) -> EpisodeRecord:
        return EpisodeRecord(
            episode_uuid=row[0],
            podcast_uuid=row[1],
            show_name=row[2],
            local_path=row[3],
            played=bool(row[4]),
            played_up_to=row[5],
            downloaded_at=row[6],
            title=row[7],
            audio_url=row[8],
            duration_seconds=row[9],
            pending_push=bool(row[10]),
            unsubscribed=bool(row[11]),
            description=row[12],
            episode_number=row[13],
            season_number=row[14],
            published_at=row[15],
        )

    def get_episode(self, episode_uuid: str) -> EpisodeRecord | None:
        row = self._conn.execute(
            f"SELECT {self._EPISODE_COLUMNS} FROM episodes WHERE episode_uuid = ?",
            (episode_uuid,),
        ).fetchone()
        return self._episode_from_row(row) if row else None

    def list_episodes(self) -> list[EpisodeRecord]:
        rows = self._conn.execute(f"SELECT {self._EPISODE_COLUMNS} FROM episodes").fetchall()
        return [self._episode_from_row(row) for row in rows]

    def count_episodes(self, *, unplayed_only: bool = False) -> int:
        """How many episodes this profile's state db believes are on the
        device -- always excludes unsubscribed (podcast-manager's own
        cleanup already treats those as gone), optionally also excludes
        already-played ones. Same "believed synced" bookkeeping
        count_tracks() uses, not a fresh device scan."""
        where = "unsubscribed = 0" + (" AND played = 0" if unplayed_only else "")
        (count,) = self._conn.execute(f"SELECT COUNT(*) FROM episodes WHERE {where}").fetchone()
        return count

    def record_episode(self, record: EpisodeRecord) -> None:
        # unsubscribed is always written as 0 here, regardless of
        # record.unsubscribed -- this is the self-heal path: being called
        # at all means a real local file exists right now (just downloaded,
        # or verified already-present), which can only happen for a show
        # that's currently subscribed again, so any stale unsubscribed
        # flag from before must clear.
        self._conn.execute(
            """
            INSERT INTO episodes (episode_uuid, podcast_uuid, show_name, local_path,
                played, played_up_to, downloaded_at, title, audio_url, duration_seconds,
                unsubscribed, description, episode_number, season_number, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT (episode_uuid) DO UPDATE SET
                podcast_uuid = excluded.podcast_uuid,
                show_name = excluded.show_name,
                local_path = excluded.local_path,
                played = excluded.played,
                played_up_to = excluded.played_up_to,
                downloaded_at = excluded.downloaded_at,
                title = excluded.title,
                audio_url = excluded.audio_url,
                duration_seconds = excluded.duration_seconds,
                unsubscribed = 0,
                description = excluded.description,
                episode_number = excluded.episode_number,
                season_number = excluded.season_number,
                published_at = excluded.published_at
            """,
            (
                record.episode_uuid,
                record.podcast_uuid,
                record.show_name,
                record.local_path,
                int(record.played),
                record.played_up_to,
                record.downloaded_at,
                record.title,
                record.audio_url,
                record.duration_seconds,
                record.description,
                record.episode_number,
                record.season_number,
                record.published_at,
            ),
        )
        self._conn.commit()

    def mark_unsubscribed(self, episode_uuid: str) -> bool:
        """Records that this episode's show is no longer in the account's
        Pocket Casts subscriptions. Caller (prune_unsubscribed_shows) has
        already handled the local file; this just flags the row so
        sync-orchestrator's next device sync can find and remove the
        on-device track via build_podcast_removal_items. Idempotent.
        Returns False if no row exists for episode_uuid."""
        existing = self.get_episode(episode_uuid)
        if existing is None:
            return False
        if existing.unsubscribed:
            return True
        self._conn.execute(
            "UPDATE episodes SET unsubscribed = 1 WHERE episode_uuid = ?",
            (episode_uuid,),
        )
        self._conn.commit()
        return True

    def update_episode_metadata(
        self,
        episode_uuid: str,
        *,
        description: str,
        episode_number: int | None,
        season_number: int | None,
        published_at: str,
    ) -> bool:
        """Backfills RSS-sourced metadata for an already-recorded episode
        without touching anything else on the row (played state,
        pending_push, unsubscribed...). Deliberately separate from
        record_episode(), which always overwrites played/played_up_to
        from its caller and would risk clobbering play state for an
        episode that's no longer an active sync candidate — exactly the
        case this exists for: backfilling episodes record_episode()
        itself never revisits once they've aged out of a show's
        candidate window. Returns False if no row exists for
        episode_uuid."""
        existing = self.get_episode(episode_uuid)
        if existing is None:
            return False
        self._conn.execute(
            "UPDATE episodes SET description = ?, episode_number = ?, "
            "season_number = ?, published_at = ? WHERE episode_uuid = ?",
            (description, episode_number, season_number, published_at, episode_uuid),
        )
        self._conn.commit()
        return True

    def update_play_state(self, episode_uuid: str, *, played: bool, played_up_to: int) -> bool:
        """Records a device-derived play-state change and marks it
        pending_push, but only if it actually differs from what's already
        recorded — avoids flagging every episode as pending on every sync
        just because it was seen again with unchanged state. Returns False
        if no row exists for episode_uuid (nothing to update).

        Only ever merges upward (OR's played, max's played_up_to) — same
        reasoning as record_remote_play_state, and just as necessary here:
        confirmed live (2026-08-18) that a raw overwrite let stray/minor
        on-device activity (e.g. a few seconds of incidental playback,
        well under PLAYED_THRESHOLD) silently downgrade an episode a user
        had already finished through Pocket Casts or a manual override
        back to unplayed on every subsequent device sync, before the
        pending_push it should have triggered ever got a chance to reach
        Pocket Casts. A real device read-back should only ever be able to
        *confirm* a play, never erase one a more authoritative source
        already recorded."""
        existing = self.get_episode(episode_uuid)
        if existing is None:
            return False
        merged_played = existing.played or played
        merged_played_up_to = max(existing.played_up_to, played_up_to)
        if existing.played == merged_played and existing.played_up_to == merged_played_up_to:
            return True
        self._conn.execute(
            "UPDATE episodes SET played = ?, played_up_to = ?, pending_push = 1 "
            "WHERE episode_uuid = ?",
            (int(merged_played), merged_played_up_to, episode_uuid),
        )
        self._conn.commit()
        return True

    def record_remote_play_state(self, episode_uuid: str, *, played: bool, played_up_to: int) -> bool:
        """Refreshes played/played_up_to from a remote (Pocket Casts)
        signal, WITHOUT marking pending_push — unlike update_play_state
        (which is for device-derived changes that Pocket Casts doesn't
        know about yet and so must be pushed to it), Pocket Casts is
        already the source of this value, so there is nothing to push
        back. Only ever merges upward (OR's played, max's played_up_to)
        so a remote read can never downgrade state a device read-back
        already confirmed. Returns False if no row exists for
        episode_uuid (nothing to refresh — the episode isn't downloaded)."""
        existing = self.get_episode(episode_uuid)
        if existing is None:
            return False
        merged_played = existing.played or played
        merged_played_up_to = max(existing.played_up_to, played_up_to)
        if existing.played == merged_played and existing.played_up_to == merged_played_up_to:
            return False
        self._conn.execute(
            "UPDATE episodes SET played = ?, played_up_to = ? WHERE episode_uuid = ?",
            (int(merged_played), merged_played_up_to, episode_uuid),
        )
        self._conn.commit()
        return True

    def list_episodes_pending_push(self) -> list[EpisodeRecord]:
        rows = self._conn.execute(
            f"SELECT {self._EPISODE_COLUMNS} FROM episodes WHERE pending_push = 1"
        ).fetchall()
        return [self._episode_from_row(row) for row in rows]

    def clear_pending_push(self, episode_uuid: str) -> None:
        self._conn.execute(
            "UPDATE episodes SET pending_push = 0 WHERE episode_uuid = ?", (episode_uuid,)
        )
        self._conn.commit()

    def get_last_fetched(self, target_type: str, target_id: str) -> datetime | None:
        """target_type is "playlist" | "podcast_show"; target_id is a
        playlist name, a show name/UUID, or the "__all__" sentinel used
        when podcasts.shows == "all". No `profile` column: this db is
        already one-file-per-profile (see resolve_roots), so scoping by
        profile here would be redundant. Used by common.schedule's
        is_due/is_due_within to decide whether a target's cron schedule
        has come due."""
        row = self._conn.execute(
            "SELECT last_fetched_at FROM fetch_runs WHERE target_type = ? AND target_id = ?",
            (target_type, target_id),
        ).fetchone()
        return datetime.fromisoformat(row[0]) if row else None

    def record_fetch(self, target_type: str, target_id: str, when: datetime) -> None:
        self._conn.execute(
            """
            INSERT INTO fetch_runs (target_type, target_id, last_fetched_at)
            VALUES (?, ?, ?)
            ON CONFLICT (target_type, target_id) DO UPDATE SET
                last_fetched_at = excluded.last_fetched_at
            """,
            (target_type, target_id, when.isoformat()),
        )
        self._conn.commit()
