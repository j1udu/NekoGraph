"""Dashboard conversation metadata kept separate from LangGraph state."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite


class ConversationMetadataStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path) -> AsyncGenerator[ConversationMetadataStore]:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as connection:
            store = cls(connection)
            await store.setup()
            yield store

    async def setup(self) -> None:
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS conversation_response_times (
                conversation_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                response_time_ms INTEGER NOT NULL,
                PRIMARY KEY (conversation_id, turn_index)
            )
            """
        )
        await self._connection.commit()

    async def record_response_time(
        self, conversation_id: str, turn_index: int, response_time_ms: int
    ) -> None:
        await self._connection.execute(
            """
            INSERT INTO conversation_response_times(
                conversation_id, turn_index, response_time_ms
            ) VALUES (?, ?, ?)
            ON CONFLICT(conversation_id, turn_index) DO UPDATE SET
                response_time_ms = excluded.response_time_ms
            """,
            (conversation_id, turn_index, response_time_ms),
        )
        await self._connection.commit()

    async def response_times(self, conversation_id: str) -> dict[int, int]:
        cursor = await self._connection.execute(
            """
            SELECT turn_index, response_time_ms
            FROM conversation_response_times
            WHERE conversation_id = ?
            ORDER BY turn_index
            """,
            (conversation_id,),
        )
        rows = await cursor.fetchall()
        return {int(row[0]): int(row[1]) for row in rows}

    async def delete(self, conversation_id: str) -> None:
        await self._connection.execute(
            "DELETE FROM conversation_response_times WHERE conversation_id = ?",
            (conversation_id,),
        )
        await self._connection.commit()
