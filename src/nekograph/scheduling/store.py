"""SQLite persistence for scheduled tasks and their execution history."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import aiosqlite

from nekograph.scheduling.models import (
    ScheduledTask,
    ScheduledTaskInput,
    ScheduleKind,
    TaskRun,
    TaskStatus,
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


class TaskStore:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path) -> AsyncGenerator[TaskStore]:
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
            CREATE TABLE IF NOT EXISTS scheduled_tasks (
                task_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                handler_name TEXT NOT NULL,
                schedule_kind TEXT NOT NULL,
                cron_expression TEXT,
                interval_seconds INTEGER,
                run_at TEXT,
                timezone TEXT NOT NULL,
                payload TEXT NOT NULL,
                enabled INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_run_at TEXT,
                next_run_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_runs (
                run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                scheduled_at TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                error TEXT,
                duration_ms INTEGER,
                FOREIGN KEY(task_id) REFERENCES scheduled_tasks(task_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_task_runs_task_started
                ON task_runs(task_id, started_at DESC);
            """
        )
        await self._connection.commit()

    async def create(self, data: ScheduledTaskInput) -> ScheduledTask:
        task_id = uuid4().hex
        now = _now()
        status = TaskStatus.SCHEDULED if data.enabled else TaskStatus.DISABLED
        await self._connection.execute(
            """
            INSERT INTO scheduled_tasks(
                task_id, name, handler_name, schedule_kind, cron_expression,
                interval_seconds, run_at, timezone, payload, enabled, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id, data.name, data.handler_name, data.schedule_kind.value,
                data.cron_expression, data.interval_seconds, _iso(data.run_at),
                data.timezone, json.dumps(data.payload, ensure_ascii=False),
                int(data.enabled), status.value, _iso(now), _iso(now),
            ),
        )
        await self._connection.commit()
        task = await self.get(task_id)
        assert task is not None
        return task

    async def get(self, task_id: str) -> ScheduledTask | None:
        cursor = await self._connection.execute(
            "SELECT * FROM scheduled_tasks WHERE task_id = ?", (task_id,)
        )
        row = await cursor.fetchone()
        return self._task(row) if row is not None else None

    async def list(self) -> list[ScheduledTask]:
        cursor = await self._connection.execute(
            "SELECT * FROM scheduled_tasks ORDER BY created_at DESC"
        )
        return [self._task(row) for row in await cursor.fetchall()]

    async def update(self, task_id: str, data: ScheduledTaskInput) -> ScheduledTask | None:
        now = _now()
        status = TaskStatus.SCHEDULED if data.enabled else TaskStatus.DISABLED
        cursor = await self._connection.execute(
            """
            UPDATE scheduled_tasks SET name=?, handler_name=?, schedule_kind=?,
                cron_expression=?, interval_seconds=?, run_at=?, timezone=?,
                payload=?, enabled=?, status=?, next_run_at=NULL,
                last_error=NULL, updated_at=?
            WHERE task_id=?
            """,
            (
                data.name, data.handler_name, data.schedule_kind.value,
                data.cron_expression, data.interval_seconds, _iso(data.run_at),
                data.timezone, json.dumps(data.payload, ensure_ascii=False),
                int(data.enabled), status.value, _iso(now), task_id,
            ),
        )
        await self._connection.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get(task_id)

    async def delete(self, task_id: str) -> bool:
        cursor = await self._connection.execute(
            "DELETE FROM scheduled_tasks WHERE task_id = ?", (task_id,)
        )
        await self._connection.commit()
        return cursor.rowcount > 0

    async def update_runtime(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        last_run_at: datetime | None = None,
        next_run_at: datetime | None = None,
        last_error: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        await self._connection.execute(
            """
            UPDATE scheduled_tasks
            SET status=?, enabled=COALESCE(?, enabled),
                last_run_at=COALESCE(?, last_run_at), next_run_at=?,
                last_error=?, updated_at=?
            WHERE task_id=?
            """,
            (
                status.value,
                None if enabled is None else int(enabled),
                _iso(last_run_at),
                _iso(next_run_at),
                last_error,
                _iso(_now()),
                task_id,
            ),
        )
        await self._connection.commit()

    async def add_run(self, run: TaskRun) -> None:
        await self._connection.execute(
            """
            INSERT OR REPLACE INTO task_runs(
                run_id, task_id, scheduled_at, started_at, finished_at,
                status, error, duration_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.run_id, run.task_id, _iso(run.scheduled_at), _iso(run.started_at),
                _iso(run.finished_at), run.status.value, run.error, run.duration_ms,
            ),
        )
        await self._connection.commit()

    async def list_runs(self, task_id: str, limit: int = 50) -> list[TaskRun]:
        cursor = await self._connection.execute(
            """
            SELECT * FROM task_runs WHERE task_id = ?
            ORDER BY started_at DESC LIMIT ?
            """,
            (task_id, limit),
        )
        return [self._run(row) for row in await cursor.fetchall()]

    @staticmethod
    def _task(row: aiosqlite.Row) -> ScheduledTask:
        payload = json.loads(str(row["payload"]))
        if not isinstance(payload, dict):
            payload = {}
        created_at = _datetime(row["created_at"])
        updated_at = _datetime(row["updated_at"])
        assert created_at is not None and updated_at is not None
        return ScheduledTask(
            task_id=str(row["task_id"]), name=str(row["name"]),
            handler_name=str(row["handler_name"]),
            schedule_kind=ScheduleKind(str(row["schedule_kind"])),
            cron_expression=row["cron_expression"],
            interval_seconds=row["interval_seconds"], run_at=_datetime(row["run_at"]),
            timezone=str(row["timezone"]), payload=cast(dict[str, Any], payload),
            enabled=bool(row["enabled"]), status=TaskStatus(str(row["status"])),
            last_run_at=_datetime(row["last_run_at"]), next_run_at=_datetime(row["next_run_at"]),
            last_error=row["last_error"], created_at=created_at, updated_at=updated_at,
        )

    @staticmethod
    def _run(row: aiosqlite.Row) -> TaskRun:
        scheduled_at = _datetime(row["scheduled_at"])
        started_at = _datetime(row["started_at"])
        assert scheduled_at is not None and started_at is not None
        return TaskRun(
            run_id=str(row["run_id"]), task_id=str(row["task_id"]),
            scheduled_at=scheduled_at, started_at=started_at,
            finished_at=_datetime(row["finished_at"]), status=TaskStatus(str(row["status"])),
            error=row["error"], duration_ms=row["duration_ms"],
        )
