"""Durable claims preventing approval-triggered tool re-execution."""

from __future__ import annotations

import json
from dataclasses import dataclass

import aiosqlite

from nekograph.tools import ToolResult, ToolResultCode


@dataclass(frozen=True, slots=True)
class ExecutionRecord:
    approval_id: str
    conversation_id: str
    tool_name: str
    status: str
    result: ToolResult | None


class ToolExecutionLedger:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    async def setup(self) -> None:
        await self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_executions (
                approval_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                status TEXT NOT NULL,
                result_json TEXT
            )
            """
        )
        await self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tool_executions_conversation
            ON tool_executions (conversation_id)
            """
        )
        await self._connection.commit()

    async def claim(self, *, approval_id: str, conversation_id: str, tool_name: str) -> bool:
        cursor = await self._connection.execute(
            """
            INSERT OR IGNORE INTO tool_executions (
                approval_id, conversation_id, tool_name, status, result_json
            ) VALUES (?, ?, ?, 'started', NULL)
            """,
            (approval_id, conversation_id, tool_name),
        )
        await self._connection.commit()
        return cursor.rowcount == 1

    async def complete(self, approval_id: str, result: ToolResult) -> None:
        encoded = json.dumps(
            {
                "tool_name": result.tool_name,
                "code": result.code,
                "output": result.output,
                "error": result.error,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self._connection.execute(
            """
            UPDATE tool_executions
            SET status = 'completed', result_json = ?
            WHERE approval_id = ?
            """,
            (encoded, approval_id),
        )
        await self._connection.commit()

    async def get(self, approval_id: str) -> ExecutionRecord | None:
        cursor = await self._connection.execute(
            """
            SELECT conversation_id, tool_name, status, result_json
            FROM tool_executions
            WHERE approval_id = ?
            """,
            (approval_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        conversation_id = str(row[0])
        tool_name = str(row[1])
        status = str(row[2])
        result = None
        if row[3] is not None:
            payload = json.loads(str(row[3]))
            result = ToolResult(
                tool_name=str(payload["tool_name"]),
                code=ToolResultCode(str(payload["code"])),
                output=payload.get("output"),
                error=payload.get("error"),
            )
        return ExecutionRecord(
            approval_id=approval_id,
            conversation_id=conversation_id,
            tool_name=tool_name,
            status=status,
            result=result,
        )

    async def clear_conversation(self, conversation_id: str) -> None:
        await self._connection.execute(
            "DELETE FROM tool_executions WHERE conversation_id = ?",
            (conversation_id,),
        )
        await self._connection.commit()
