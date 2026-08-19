"""APScheduler-backed runtime for persistent, named task handlers."""

# APScheduler 3 does not ship type stubs. Keep its untyped boundary local to this module.

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[reportMissingTypeStubs]
from apscheduler.triggers.cron import CronTrigger  # type: ignore[reportMissingTypeStubs]
from apscheduler.triggers.date import DateTrigger  # type: ignore[reportMissingTypeStubs]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[reportMissingTypeStubs]

from nekograph.scheduling.models import (
    ScheduledTask,
    ScheduledTaskInput,
    ScheduleKind,
    TaskRun,
    TaskStatus,
)
from nekograph.scheduling.registry import TaskHandlerContext, TaskHandlerRegistry
from nekograph.scheduling.store import TaskStore

logger = logging.getLogger(__name__)

_CRONTAB_WEEKDAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")
_CRONTAB_WEEKDAY_PATTERN = re.compile(r"^(?:(\*)|(\d+)(?:-(\d+))?)(?:/(\d+))?$")


class SchedulingError(ValueError):
    """Raised when a task cannot be validated or scheduled."""


def _normalize_crontab_day_of_week(value: str) -> str:
    """Translate standard crontab Sunday=0/7 to APScheduler weekday names."""
    parts: list[str] = []
    for raw in value.split(","):
        match = _CRONTAB_WEEKDAY_PATTERN.fullmatch(raw.strip().lower())
        if match is None:
            parts.append(raw.strip())
            continue
        wildcard, start_text, end_text, step_text = match.groups()
        step = int(step_text or "1")
        if wildcard:
            values = range(0, 7, step)
        else:
            start = int(start_text)
            end = int(end_text) if end_text is not None else (7 if step_text else start)
            if not 0 <= start <= 7 or not 0 <= end <= 7 or start > end:
                raise SchedulingError("cron weekday must be between 0 and 7")
            values = range(start, end + 1, step)
        weekdays = {0 if item == 7 else item for item in values}
        parts.extend(_CRONTAB_WEEKDAY_NAMES[item] for item in sorted(weekdays))
    return ",".join(parts)


class SchedulerRuntime:
    def __init__(
        self,
        store: TaskStore,
        registry: TaskHandlerRegistry,
        *,
        max_concurrency: int = 8,
    ) -> None:
        self.store = store
        self.registry = registry
        self.scheduler: Any = AsyncIOScheduler(timezone=UTC)
        self._locks: dict[str, asyncio.Lock] = {}
        self._global_semaphore = asyncio.Semaphore(max_concurrency)
        self._lifecycle_lock = asyncio.Lock()
        self._started = False

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        path: Path,
        registry: TaskHandlerRegistry,
        *,
        max_concurrency: int = 8,
    ) -> AsyncGenerator[SchedulerRuntime]:
        async with TaskStore.open(path) as store:
            runtime = cls(store, registry, max_concurrency=max_concurrency)
            await runtime.start()
            try:
                yield runtime
            finally:
                await runtime.shutdown()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started:
                return
            self.scheduler.start()
            self._started = True
            for task in await self.store.list():
                if not task.enabled:
                    continue
                if self.registry.get(task.handler_name) is None:
                    await self.store.update_runtime(
                        task.task_id,
                        status=TaskStatus.UNAVAILABLE,
                        last_error=f"handler not registered: {task.handler_name}",
                    )
                    continue
                try:
                    self._schedule(task)
                    await self._refresh_next_run(task.task_id)
                except SchedulingError as exc:
                    await self.store.update_runtime(
                        task.task_id, status=TaskStatus.FAILED, last_error=str(exc)
                    )
                    logger.exception(
                        "scheduled_task_restore_failed", extra={"task_id": task.task_id}
                    )

    async def shutdown(self) -> None:
        async with self._lifecycle_lock:
            if not self._started:
                return
            self.scheduler.shutdown(wait=False)
            self._started = False

    async def create(self, data: ScheduledTaskInput) -> ScheduledTask:
        self._validate(data)
        self._require_handler(data.handler_name)
        self._build_trigger(data)
        task = await self.store.create(data)
        if task.enabled:
            try:
                self._schedule(task)
            except SchedulingError:
                await self.store.delete(task.task_id)
                raise
            await self._refresh_next_run(task.task_id)
        return (await self.store.get(task.task_id)) or task

    async def update(self, task_id: str, data: ScheduledTaskInput) -> ScheduledTask:
        self._validate(data)
        self._require_handler(data.handler_name)
        self._build_trigger(data)
        previous = await self.store.get(task_id)
        if previous is None:
            raise SchedulingError(f"scheduled task not found: {task_id}")
        self._remove_schedule(task_id)
        task = await self.store.update(task_id, data)
        assert task is not None
        if task.enabled:
            try:
                self._schedule(task)
            except SchedulingError:
                # Keep the database definition but mark it unusable for diagnosis.
                await self.store.update_runtime(
                    task_id, status=TaskStatus.FAILED, last_error="invalid schedule"
                )
                raise
            await self._refresh_next_run(task_id)
        return (await self.store.get(task_id)) or task

    async def delete(self, task_id: str) -> None:
        self._remove_schedule(task_id)
        self._locks.pop(task_id, None)
        if not await self.store.delete(task_id):
            raise SchedulingError(f"scheduled task not found: {task_id}")

    async def list(self) -> list[ScheduledTask]:
        return await self.store.list()

    async def runs(self, task_id: str, limit: int = 50) -> list[TaskRun]:
        if await self.store.get(task_id) is None:
            raise SchedulingError(f"scheduled task not found: {task_id}")
        return await self.store.list_runs(task_id, limit)

    async def run_now(self, task_id: str) -> None:
        if await self.store.get(task_id) is None:
            raise SchedulingError(f"scheduled task not found: {task_id}")
        await self._execute(task_id, ignore_enabled=True, consume_once=False)

    def handler_names(self) -> tuple[str, ...]:
        return self.registry.names()

    def next_run_at(self, task_id: str) -> datetime | None:
        job = self.scheduler.get_job(task_id)
        return getattr(job, "next_run_time", None)

    def _schedule(self, task: ScheduledTask) -> None:
        if not self._started:
            raise SchedulingError("scheduler is not started")
        trigger = self._build_trigger(task)
        self.scheduler.add_job(
            self._execute, trigger=trigger, id=task.task_id, args=[task.task_id],
            replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=30,
        )

    @staticmethod
    def _build_trigger(task: ScheduledTaskInput) -> Any:
        try:
            zone = ZoneInfo(task.timezone)
            if task.schedule_kind is ScheduleKind.CRON:
                assert task.cron_expression is not None
                fields = task.cron_expression.split()
                if len(fields) != 5:
                    raise SchedulingError("cron expression must contain five fields")
                fields[-1] = _normalize_crontab_day_of_week(fields[-1])
                cron_trigger: Any = CronTrigger
                return cron_trigger.from_crontab(" ".join(fields), timezone=zone)
            if task.schedule_kind is ScheduleKind.INTERVAL:
                assert task.interval_seconds is not None
                return IntervalTrigger(seconds=task.interval_seconds, timezone=zone)
            assert task.run_at is not None
            return DateTrigger(run_date=task.run_at, timezone=zone)
        except ZoneInfoNotFoundError as exc:
            raise SchedulingError(f"unknown timezone: {task.timezone}") from exc
        except (TypeError, ValueError) as exc:
            raise SchedulingError(str(exc)) from exc

    def _remove_schedule(self, task_id: str) -> None:
        if self.scheduler.get_job(task_id) is not None:
            self.scheduler.remove_job(task_id)

    async def _refresh_next_run(self, task_id: str) -> None:
        job = self.scheduler.get_job(task_id)
        next_run: datetime | None = getattr(job, "next_run_time", None)
        await self.store.update_runtime(task_id, status=TaskStatus.SCHEDULED, next_run_at=next_run)

    async def _execute(
        self,
        task_id: str,
        *,
        ignore_enabled: bool = False,
        consume_once: bool = True,
    ) -> None:
        task = await self.store.get(task_id)
        if task is None or (not task.enabled and not ignore_enabled):
            return
        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock, self._global_semaphore:
            await self._execute_locked(
                task_id,
                ignore_enabled=ignore_enabled,
                consume_once=consume_once,
            )

    async def _execute_locked(
        self,
        task_id: str,
        *,
        ignore_enabled: bool,
        consume_once: bool,
    ) -> None:
        task = await self.store.get(task_id)
        if task is None or (not task.enabled and not ignore_enabled):
            return
        handler = self.registry.get(task.handler_name)
        if handler is None:
            await self.store.update_runtime(
                task_id,
                status=TaskStatus.UNAVAILABLE,
                last_error=f"handler not registered: {task.handler_name}",
            )
            return
        started = datetime.now(UTC)
        run = TaskRun(
            run_id=uuid4().hex,
            task_id=task_id,
            scheduled_at=started,
            started_at=started,
            status=TaskStatus.RUNNING,
        )
        await self.store.add_run(run)
        await self.store.update_runtime(
            task_id, status=TaskStatus.RUNNING, last_run_at=started, last_error=None
        )
        status = TaskStatus.COMPLETED
        error: str | None = None
        try:
            await handler(
                TaskHandlerContext(run_id=run.run_id, task=task, payload=task.payload)
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            status = TaskStatus.FAILED
            error = str(exc)
            logger.exception(
                "scheduled_task_failed", extra={"task_id": task_id, "run_id": run.run_id}
            )
        finished = datetime.now(UTC)
        await self.store.add_run(
            run.model_copy(
                update={
                    "finished_at": finished,
                    "status": status,
                    "error": error,
                    "duration_ms": max(
                        0, int((finished - started).total_seconds() * 1000)
                    ),
                }
            )
        )
        job = self.scheduler.get_job(task_id)
        next_run: datetime | None = getattr(job, "next_run_time", None)
        if task.schedule_kind is ScheduleKind.ONCE and consume_once:
            self._remove_schedule(task_id)
            next_run = None
        await self.store.update_runtime(
            task_id,
            status=status,
            enabled=(
                False if task.schedule_kind is ScheduleKind.ONCE and consume_once else None
            ),
            last_run_at=started,
            next_run_at=next_run,
            last_error=error,
        )

    def _require_handler(self, name: str) -> None:
        if self.registry.get(name) is None:
            raise SchedulingError(f"handler not registered: {name}")

    @staticmethod
    def _validate(data: ScheduledTaskInput) -> None:
        if data.schedule_kind is ScheduleKind.CRON and not data.cron_expression:
            raise SchedulingError("cron_expression is required for cron tasks")
        if data.schedule_kind is ScheduleKind.INTERVAL and data.interval_seconds is None:
            raise SchedulingError("interval_seconds is required for interval tasks")
        if data.schedule_kind is ScheduleKind.ONCE and data.run_at is None:
            raise SchedulingError("run_at is required for once tasks")
        if data.schedule_kind is not ScheduleKind.CRON and data.cron_expression is not None:
            raise SchedulingError("cron_expression is only valid for cron tasks")
        if data.schedule_kind is not ScheduleKind.INTERVAL and data.interval_seconds is not None:
            raise SchedulingError("interval_seconds is only valid for interval tasks")
        if data.schedule_kind is not ScheduleKind.ONCE and data.run_at is not None:
            raise SchedulingError("run_at is only valid for once tasks")
