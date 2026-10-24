"""SQLite persistence: dedupe ledger, drafts, publish log and key/value state."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_posts (
    channel      TEXT NOT NULL,
    message_id   INTEGER NOT NULL,
    content_hash TEXT,
    seen_at      TEXT NOT NULL,
    PRIMARY KEY (channel, message_id)
);
CREATE INDEX IF NOT EXISTS idx_seen_hash ON seen_posts(content_hash);

CREATE TABLE IF NOT EXISTS drafts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_channel    TEXT NOT NULL,
    source_message_id INTEGER NOT NULL,
    source_link       TEXT,
    text              TEXT NOT NULL,
    source_text       TEXT,
    media_kind        TEXT,
    media_path        TEXT,
    status            TEXT NOT NULL DEFAULT 'pending',
    created_at        TEXT NOT NULL,
    published_at      TEXT,
    target_message_id INTEGER,
    content_hash      TEXT,
    notes             TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_drafts_status ON drafts(status);

CREATE TABLE IF NOT EXISTS publishes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id          INTEGER,
    target_message_id INTEGER,
    content_hash      TEXT,
    publish_date      TEXT NOT NULL,
    published_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pub_date ON publishes(publish_date);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin async wrapper around a single SQLite connection."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> Database:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()
        return self

    async def _migrate(self) -> None:
        """Add columns that were introduced after a database was created."""
        cursor = await self.conn.execute("PRAGMA table_info(drafts)")
        existing = {row["name"] for row in await cursor.fetchall()}
        for column in ("source_text", "content_hash"):
            if column not in existing:
                await self.conn.execute(f"ALTER TABLE drafts ADD COLUMN {column} TEXT")

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> Database:
        return await self.connect()

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not connected")
        return self._conn

    # ------------------------------------------------------------------- seen
    async def is_seen(self, channel: str, message_id: int) -> bool:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT 1 FROM seen_posts WHERE channel = ? AND message_id = ?",
                (channel, message_id),
            )
            return await cursor.fetchone() is not None

    async def mark_seen(
        self, channel: str, message_id: int, content_hash: str | None = None
    ) -> None:
        async with self._lock:
            await self.conn.execute(
                "INSERT OR IGNORE INTO seen_posts (channel, message_id, content_hash, seen_at) "
                "VALUES (?, ?, ?, ?)",
                (channel, message_id, content_hash, iso(utcnow())),
            )
            await self.conn.commit()

    async def hash_seen_recently(self, content_hash: str, hours: int) -> bool:
        """Has the same idea (same normalised text) been handled recently?"""
        if not content_hash:
            return False
        cutoff = iso(utcnow() - timedelta(hours=max(hours, 1)))
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT 1 FROM seen_posts WHERE content_hash = ? AND seen_at >= ? LIMIT 1",
                (content_hash, cutoff),
            )
            row = await cursor.fetchone()
            if row is not None:
                return True
            cursor = await self.conn.execute(
                "SELECT 1 FROM drafts WHERE content_hash = ? AND created_at >= ? LIMIT 1",
                (content_hash, cutoff),
            )
            return await cursor.fetchone() is not None

    async def recent_source_texts(self, hours: int, limit: int = 60) -> list[tuple[str, str]]:
        """(source_text, status) of drafts created recently – used for near-duplicate checks."""
        cutoff = iso(utcnow() - timedelta(hours=max(hours, 1)))
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT source_text, status FROM drafts "
                "WHERE created_at >= ? AND source_text IS NOT NULL "
                "ORDER BY id DESC LIMIT ?",
                (cutoff, limit),
            )
            rows = await cursor.fetchall()
        return [(row["source_text"], row["status"]) for row in rows]

    async def purge_seen(self, older_than_days: int = 60) -> int:
        cutoff = iso(utcnow() - timedelta(days=older_than_days))
        async with self._lock:
            cursor = await self.conn.execute(
                "DELETE FROM seen_posts WHERE seen_at < ?", (cutoff,)
            )
            await self.conn.commit()
            return cursor.rowcount or 0

    # ----------------------------------------------------------------- drafts
    async def create_draft(
        self,
        *,
        source_channel: str,
        source_message_id: int,
        text: str,
        media_kind: str | None = None,
        media_path: str | None = None,
        source_link: str | None = None,
        content_hash: str | None = None,
        status: str = "pending",
        notes: str = "",
        source_text: str | None = None,
    ) -> int:
        async with self._lock:
            cursor = await self.conn.execute(
                """INSERT INTO drafts
                   (source_channel, source_message_id, source_link, text, source_text,
                    media_kind, media_path, status, created_at, content_hash, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    source_channel,
                    source_message_id,
                    source_link,
                    text,
                    source_text,
                    media_kind,
                    media_path,
                    status,
                    iso(utcnow()),
                    content_hash,
                    notes,
                ),
            )
            await self.conn.commit()
            return int(cursor.lastrowid or 0)

    async def get_draft(self, draft_id: int) -> aiosqlite.Row | None:
        async with self._lock:
            cursor = await self.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,))
            return await cursor.fetchone()

    async def update_draft(self, draft_id: int, **fields: object) -> None:
        if not fields:
            return
        allowed = {
            "text",
            "media_kind",
            "media_path",
            "status",
            "published_at",
            "target_message_id",
            "notes",
            "source_link",
            "source_text",
        }
        columns = [key for key in fields if key in allowed]
        if not columns:
            return
        assignments = ", ".join(f"{col} = ?" for col in columns)
        values = [fields[col] for col in columns] + [draft_id]
        async with self._lock:
            await self.conn.execute(f"UPDATE drafts SET {assignments} WHERE id = ?", values)
            await self.conn.commit()

    async def list_drafts(self, status: str | None = None, limit: int = 20) -> list[aiosqlite.Row]:
        query = "SELECT * FROM drafts"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self._lock:
            cursor = await self.conn.execute(query, params)
            return list(await cursor.fetchall())

    async def expire_pending(self, older_than_hours: int = 24) -> int:
        cutoff = iso(utcnow() - timedelta(hours=older_than_hours))
        async with self._lock:
            cursor = await self.conn.execute(
                "UPDATE drafts SET status = 'expired' "
                "WHERE status = 'pending' AND created_at < ?",
                (cutoff,),
            )
            await self.conn.commit()
            return cursor.rowcount or 0

    # --------------------------------------------------------------- publishes
    async def record_publish(
        self,
        *,
        draft_id: int | None,
        target_message_id: int | None,
        content_hash: str | None,
        publish_date: str,
    ) -> None:
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO publishes (draft_id, target_message_id, content_hash, "
                "publish_date, published_at) VALUES (?, ?, ?, ?, ?)",
                (draft_id, target_message_id, content_hash, publish_date, iso(utcnow())),
            )
            await self.conn.commit()

    async def count_published_on(self, publish_date: str) -> int:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT COUNT(*) FROM publishes WHERE publish_date = ?", (publish_date,)
            )
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def last_publish_time(self) -> datetime | None:
        async with self._lock:
            cursor = await self.conn.execute(
                "SELECT published_at FROM publishes ORDER BY id DESC LIMIT 1"
            )
            row = await cursor.fetchone()
        if not row:
            return None
        try:
            return datetime.fromisoformat(row[0])
        except ValueError:
            return None

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            out: dict[str, int] = {}
            for label, query in (
                ("seen", "SELECT COUNT(*) FROM seen_posts"),
                ("pending", "SELECT COUNT(*) FROM drafts WHERE status = 'pending'"),
                ("published", "SELECT COUNT(*) FROM publishes"),
                ("rejected", "SELECT COUNT(*) FROM drafts WHERE status = 'rejected'"),
            ):
                cursor = await self.conn.execute(query)
                row = await cursor.fetchone()
                out[label] = int(row[0]) if row else 0
            return out

    # --------------------------------------------------------------------- kv
    async def kv_get(self, key: str, default: str = "") -> str:
        async with self._lock:
            cursor = await self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,))
            row = await cursor.fetchone()
        return row[0] if row else default

    async def kv_set(self, key: str, value: str) -> None:
        async with self._lock:
            await self.conn.execute(
                "INSERT INTO kv (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            await self.conn.commit()

    async def is_paused(self) -> bool:
        return await self.kv_get("paused", "0") == "1"

    async def set_paused(self, paused: bool) -> None:
        await self.kv_set("paused", "1" if paused else "0")
