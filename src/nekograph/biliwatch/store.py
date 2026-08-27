"""SQLite persistence for BiliWatch subscriptions, content, and deliveries."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import aiosqlite

from nekograph.biliwatch.models import (
    DeliveryRecord,
    DeliveryStatus,
    PendingDelivery,
    StoredContent,
    Subscription,
    SubscriptionInput,
    WatchType,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None


def _datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


class BiliWatchStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self._write_lock = asyncio.Lock()

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path) -> AsyncGenerator[BiliWatchStore]:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as connection:
            connection.row_factory = aiosqlite.Row
            await connection.execute("PRAGMA foreign_keys = ON")
            store = cls(connection)
            await store.setup()
            yield store

    async def setup(self) -> None:
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS biliwatch_subscriptions (
                subscription_id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                uid TEXT NOT NULL,
                uname TEXT NOT NULL,
                watch_dynamic INTEGER NOT NULL,
                watch_live INTEGER NOT NULL,
                at_all_dynamic INTEGER NOT NULL,
                at_all_live INTEGER NOT NULL,
                filter_forward INTEGER NOT NULL,
                enabled INTEGER NOT NULL,
                last_dynamic_timestamp INTEGER,
                was_live INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(bot_id, group_id, uid)
            );
            CREATE INDEX IF NOT EXISTS idx_biliwatch_subscriptions_uid
                ON biliwatch_subscriptions(uid, enabled);

            CREATE TABLE IF NOT EXISTS biliwatch_contents (
                content_key TEXT PRIMARY KEY,
                uid TEXT NOT NULL,
                kind TEXT NOT NULL,
                published_at TEXT NOT NULL,
                payload TEXT NOT NULL,
                discovered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS biliwatch_deliveries (
                delivery_id TEXT PRIMARY KEY,
                subscription_id TEXT NOT NULL,
                content_key TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                message_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                sent_at TEXT,
                UNIQUE(subscription_id, content_key),
                FOREIGN KEY(subscription_id) REFERENCES biliwatch_subscriptions(subscription_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(content_key) REFERENCES biliwatch_contents(content_key)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_biliwatch_deliveries_status
                ON biliwatch_deliveries(status, updated_at);
            """
        )
        await self._connection.commit()

    async def save_subscription(
        self,
        data: SubscriptionInput,
        *,
        uname: str,
        last_dynamic_timestamp: int | None = None,
    ) -> Subscription:
        existing = await self.by_target(data.bot_id, data.group_id, data.uid)
        now = _now()
        async with self._write_lock:
            if existing is None:
                subscription_id = uuid4().hex
                await self._connection.execute(
                    """
                    INSERT INTO biliwatch_subscriptions(
                        subscription_id, bot_id, group_id, uid, uname,
                        watch_dynamic, watch_live, at_all_dynamic, at_all_live,
                        filter_forward, enabled, last_dynamic_timestamp, was_live,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        subscription_id,
                        data.bot_id,
                        data.group_id,
                        data.uid,
                        uname,
                        int(data.watch_dynamic),
                        int(data.watch_live),
                        int(data.at_all_dynamic),
                        int(data.at_all_live),
                        int(data.filter_forward),
                        int(data.enabled),
                        last_dynamic_timestamp,
                        _iso(now),
                        _iso(now),
                    ),
                )
            else:
                subscription_id = existing.subscription_id
                cursor = (
                    existing.last_dynamic_timestamp
                    if existing.last_dynamic_timestamp is not None
                    else last_dynamic_timestamp
                )
                await self._connection.execute(
                    """
                    UPDATE biliwatch_subscriptions SET
                        uname=?, watch_dynamic=?, watch_live=?, at_all_dynamic=?,
                        at_all_live=?, filter_forward=?, enabled=?,
                        last_dynamic_timestamp=?, updated_at=?
                    WHERE subscription_id=?
                    """,
                    (
                        uname,
                        int(data.watch_dynamic),
                        int(data.watch_live),
                        int(data.at_all_dynamic),
                        int(data.at_all_live),
                        int(data.filter_forward),
                        int(data.enabled),
                        cursor,
                        _iso(now),
                        subscription_id,
                    ),
                )
            await self._connection.commit()
        stored = await self.get(subscription_id)
        assert stored is not None
        return stored

    async def get(self, subscription_id: str) -> Subscription | None:
        cursor = await self._connection.execute(
            "SELECT * FROM biliwatch_subscriptions WHERE subscription_id=?",
            (subscription_id,),
        )
        row = await cursor.fetchone()
        return self._subscription(row) if row is not None else None

    async def by_target(
        self, bot_id: str, group_id: str, uid: str
    ) -> Subscription | None:
        cursor = await self._connection.execute(
            """
            SELECT * FROM biliwatch_subscriptions
            WHERE bot_id=? AND group_id=? AND uid=?
            """,
            (bot_id, group_id, uid),
        )
        row = await cursor.fetchone()
        return self._subscription(row) if row is not None else None

    async def subscriptions(self, *, enabled_only: bool = False) -> list[Subscription]:
        where = " WHERE enabled=1" if enabled_only else ""
        cursor = await self._connection.execute(
            f"SELECT * FROM biliwatch_subscriptions{where} ORDER BY group_id, uname"  # noqa: S608
        )
        return [self._subscription(row) for row in await cursor.fetchall()]

    async def delete_subscription(self, subscription_id: str) -> bool:
        async with self._write_lock:
            cursor = await self._connection.execute(
                "DELETE FROM biliwatch_subscriptions WHERE subscription_id=?",
                (subscription_id,),
            )
            await self._connection.commit()
            return cursor.rowcount > 0

    async def update_dynamic_cursor(
        self, subscription_id: str, timestamp: int, *, uname: str | None = None
    ) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE biliwatch_subscriptions
                SET last_dynamic_timestamp=?, uname=COALESCE(?, uname), updated_at=?
                WHERE subscription_id=?
                """,
                (timestamp, uname, _iso(_now()), subscription_id),
            )
            await self._connection.commit()

    async def update_live_state(
        self, subscription_id: str, is_live: bool, *, uname: str | None = None
    ) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE biliwatch_subscriptions
                SET was_live=?, uname=COALESCE(?, uname), updated_at=?
                WHERE subscription_id=?
                """,
                (int(is_live), uname, _iso(_now()), subscription_id),
            )
            await self._connection.commit()

    async def save_content(self, content: StoredContent) -> bool:
        async with self._write_lock:
            cursor = await self._connection.execute(
                """
                INSERT OR IGNORE INTO biliwatch_contents(
                    content_key, uid, kind, published_at, payload, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    content.content_key,
                    content.uid,
                    content.kind.value,
                    _iso(content.published_at),
                    json.dumps(content.payload, ensure_ascii=False, separators=(",", ":")),
                    _iso(content.discovered_at),
                ),
            )
            await self._connection.commit()
            return cursor.rowcount > 0

    async def ensure_delivery(
        self, subscription_id: str, content_key: str
    ) -> DeliveryRecord:
        now = _now()
        async with self._write_lock:
            await self._connection.execute(
                """
                INSERT OR IGNORE INTO biliwatch_deliveries(
                    delivery_id, subscription_id, content_key, status, attempts,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    uuid4().hex,
                    subscription_id,
                    content_key,
                    DeliveryStatus.PENDING.value,
                    _iso(now),
                    _iso(now),
                ),
            )
            await self._connection.commit()
        record = await self.delivery(subscription_id, content_key)
        assert record is not None
        return record

    async def delivery(
        self, subscription_id: str, content_key: str
    ) -> DeliveryRecord | None:
        cursor = await self._connection.execute(
            """
            SELECT d.*, s.bot_id, s.group_id, s.uid, c.kind
            FROM biliwatch_deliveries d
            JOIN biliwatch_subscriptions s USING(subscription_id)
            JOIN biliwatch_contents c USING(content_key)
            WHERE d.subscription_id=? AND d.content_key=?
            """,
            (subscription_id, content_key),
        )
        row = await cursor.fetchone()
        return self._delivery(row) if row is not None else None

    async def mark_delivery_sent(self, delivery_id: str, message_id: str) -> None:
        now = _now()
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE biliwatch_deliveries SET status=?, attempts=attempts+1,
                    message_id=?, error=NULL, sent_at=?, updated_at=?
                WHERE delivery_id=?
                """,
                (
                    DeliveryStatus.SENT.value,
                    message_id,
                    _iso(now),
                    _iso(now),
                    delivery_id,
                ),
            )
            await self._connection.commit()

    async def mark_delivery_failed(self, delivery_id: str, error: str) -> None:
        async with self._write_lock:
            await self._connection.execute(
                """
                UPDATE biliwatch_deliveries SET status=?, attempts=attempts+1,
                    error=?, updated_at=? WHERE delivery_id=?
                """,
                (DeliveryStatus.FAILED.value, error[:2000], _iso(_now()), delivery_id),
            )
            await self._connection.commit()

    async def pending_deliveries(self, *, max_attempts: int = 3) -> list[PendingDelivery]:
        cursor = await self._connection.execute(
            """
            SELECT
                d.delivery_id AS d_delivery_id, d.subscription_id AS d_subscription_id,
                d.content_key AS d_content_key, d.status AS d_status,
                d.attempts AS d_attempts, d.message_id AS d_message_id,
                d.error AS d_error, d.created_at AS d_created_at,
                d.updated_at AS d_updated_at, d.sent_at AS d_sent_at,
                s.*, c.uid AS c_uid, c.kind AS c_kind,
                c.published_at AS c_published_at, c.payload AS c_payload,
                c.discovered_at AS c_discovered_at
            FROM biliwatch_deliveries d
            JOIN biliwatch_subscriptions s USING(subscription_id)
            JOIN biliwatch_contents c USING(content_key)
            WHERE d.status IN (?, ?) AND d.attempts < ? AND s.enabled=1
            ORDER BY d.created_at
            """,
            (DeliveryStatus.PENDING.value, DeliveryStatus.FAILED.value, max_attempts),
        )
        results: list[PendingDelivery] = []
        for row in await cursor.fetchall():
            delivery = DeliveryRecord(
                delivery_id=str(row["d_delivery_id"]),
                subscription_id=str(row["d_subscription_id"]),
                content_key=str(row["d_content_key"]),
                bot_id=str(row["bot_id"]),
                group_id=str(row["group_id"]),
                uid=str(row["uid"]),
                kind=WatchType(str(row["c_kind"])),
                status=DeliveryStatus(str(row["d_status"])),
                attempts=int(row["d_attempts"]),
                message_id=row["d_message_id"],
                error=row["d_error"],
                created_at=_required_datetime(row["d_created_at"]),
                updated_at=_required_datetime(row["d_updated_at"]),
                sent_at=_datetime(row["d_sent_at"]),
            )
            content = StoredContent(
                content_key=str(row["d_content_key"]),
                uid=str(row["c_uid"]),
                kind=WatchType(str(row["c_kind"])),
                published_at=_required_datetime(row["c_published_at"]),
                payload=_json_object(row["c_payload"]),
                discovered_at=_required_datetime(row["c_discovered_at"]),
            )
            results.append(
                PendingDelivery(
                    delivery=delivery,
                    subscription=self._subscription(row),
                    content=content,
                )
            )
        return results

    async def deliveries(self, limit: int = 100) -> list[DeliveryRecord]:
        cursor = await self._connection.execute(
            """
            SELECT d.*, s.bot_id, s.group_id, s.uid, c.kind
            FROM biliwatch_deliveries d
            JOIN biliwatch_subscriptions s USING(subscription_id)
            JOIN biliwatch_contents c USING(content_key)
            ORDER BY d.created_at DESC LIMIT ?
            """,
            (limit,),
        )
        return [self._delivery(row) for row in await cursor.fetchall()]

    @staticmethod
    def _subscription(row: aiosqlite.Row) -> Subscription:
        return Subscription(
            subscription_id=str(row["subscription_id"]),
            bot_id=str(row["bot_id"]),
            group_id=str(row["group_id"]),
            uid=str(row["uid"]),
            uname=str(row["uname"]),
            watch_dynamic=bool(row["watch_dynamic"]),
            watch_live=bool(row["watch_live"]),
            at_all_dynamic=bool(row["at_all_dynamic"]),
            at_all_live=bool(row["at_all_live"]),
            filter_forward=bool(row["filter_forward"]),
            enabled=bool(row["enabled"]),
            last_dynamic_timestamp=row["last_dynamic_timestamp"],
            was_live=bool(row["was_live"]),
            created_at=_required_datetime(row["created_at"]),
            updated_at=_required_datetime(row["updated_at"]),
        )

    @staticmethod
    def _delivery(row: aiosqlite.Row) -> DeliveryRecord:
        return DeliveryRecord(
            delivery_id=str(row["delivery_id"]),
            subscription_id=str(row["subscription_id"]),
            content_key=str(row["content_key"]),
            bot_id=str(row["bot_id"]),
            group_id=str(row["group_id"]),
            uid=str(row["uid"]),
            kind=WatchType(str(row["kind"])),
            status=DeliveryStatus(str(row["status"])),
            attempts=int(row["attempts"]),
            message_id=row["message_id"],
            error=row["error"],
            created_at=_required_datetime(row["created_at"]),
            updated_at=_required_datetime(row["updated_at"]),
            sent_at=_datetime(row["sent_at"]),
        )


def _required_datetime(value: str) -> datetime:
    parsed = _datetime(value)
    assert parsed is not None
    return parsed


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return cast(dict[str, Any], parsed) if isinstance(parsed, dict) else {}
