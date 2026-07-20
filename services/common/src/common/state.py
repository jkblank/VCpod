from __future__ import annotations

import sqlite3
from dataclasses import dataclass
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
                PRIMARY KEY (episode_uuid)
            )
            """
        )
        self._migrate_episodes_columns()
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

    _EPISODE_COLUMNS = (
        "episode_uuid, podcast_uuid, show_name, local_path, played, "
        "played_up_to, downloaded_at, title, audio_url, duration_seconds, pending_push"
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

    def record_episode(self, record: EpisodeRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO episodes (episode_uuid, podcast_uuid, show_name, local_path,
                played, played_up_to, downloaded_at, title, audio_url, duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (episode_uuid) DO UPDATE SET
                podcast_uuid = excluded.podcast_uuid,
                show_name = excluded.show_name,
                local_path = excluded.local_path,
                played = excluded.played,
                played_up_to = excluded.played_up_to,
                downloaded_at = excluded.downloaded_at,
                title = excluded.title,
                audio_url = excluded.audio_url,
                duration_seconds = excluded.duration_seconds
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
            ),
        )
        self._conn.commit()

    def update_play_state(self, episode_uuid: str, *, played: bool, played_up_to: int) -> bool:
        """Records a device-derived play-state change and marks it
        pending_push, but only if it actually differs from what's already
        recorded — avoids flagging every episode as pending on every sync
        just because it was seen again with unchanged state. Returns False
        if no row exists for episode_uuid (nothing to update)."""
        existing = self.get_episode(episode_uuid)
        if existing is None:
            return False
        if existing.played == played and existing.played_up_to == played_up_to:
            return True
        self._conn.execute(
            "UPDATE episodes SET played = ?, played_up_to = ?, pending_push = 1 "
            "WHERE episode_uuid = ?",
            (int(played), played_up_to, episode_uuid),
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
