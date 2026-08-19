from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nekograph.scheduling import (
    ScheduledTaskInput,
    ScheduleKind,
    SchedulerRuntime,
    TaskHandlerRegistry,
    TaskStatus,
)
from nekograph.scheduling.registry import TaskHandlerContext


def _interval(name: str = "diagnostic") -> ScheduledTaskInput:
    return ScheduledTaskInput(
        name=name,
        handler_name="core.diagnostic",
        schedule_kind=ScheduleKind.INTERVAL,
        interval_seconds=3600,
    )


@pytest.mark.asyncio
async def test_task_persists_and_is_restored(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    registry.register("core.diagnostic", _noop)
    database = tmp_path / "scheduled.sqlite"

    async with SchedulerRuntime.open(database, registry) as runtime:
        task = await runtime.create(_interval())
        assert task.status is TaskStatus.SCHEDULED
        assert runtime.scheduler.get_job(task.task_id) is not None

    async with SchedulerRuntime.open(database, registry) as restored:
        assert restored.scheduler.get_job(task.task_id) is not None
        loaded = await restored.list()
        assert loaded[0].task_id == task.task_id


@pytest.mark.asyncio
async def test_run_now_records_success_and_failure_without_stopping_runtime(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    calls: list[str] = []

    async def handler(context: TaskHandlerContext) -> None:
        calls.append(context.run_id)

    async def broken(context: TaskHandlerContext) -> None:
        raise RuntimeError("expected failure")

    registry.register("core.diagnostic", handler)
    registry.register("core.broken", broken)
    async with SchedulerRuntime.open(tmp_path / "scheduled.sqlite", registry) as runtime:
        successful = await runtime.create(_interval())
        await runtime.run_now(successful.task_id)
        assert len(calls) == 1
        assert (await runtime.runs(successful.task_id))[0].status is TaskStatus.COMPLETED

        failed_input = _interval("broken").model_copy(
            update={"handler_name": "core.broken"}
        )
        failed = await runtime.create(failed_input)
        await runtime.run_now(failed.task_id)
        assert (await runtime.list())[0].status is TaskStatus.FAILED
        assert runtime.scheduler.running


@pytest.mark.asyncio
async def test_same_task_runs_do_not_overlap(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    active = 0
    maximum = 0

    async def handler(context: TaskHandlerContext) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1

    registry.register("core.diagnostic", handler)
    async with SchedulerRuntime.open(tmp_path / "scheduled.sqlite", registry) as runtime:
        task = await runtime.create(_interval())
        await asyncio.gather(runtime.run_now(task.task_id), runtime.run_now(task.task_id))
        assert maximum == 1
        assert len(await runtime.runs(task.task_id)) == 2


@pytest.mark.asyncio
async def test_global_concurrency_limit_applies_across_tasks(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    active = 0
    maximum = 0

    async def handler(context: TaskHandlerContext) -> None:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1

    registry.register("core.diagnostic", handler)
    async with SchedulerRuntime.open(
        tmp_path / "scheduled.sqlite", registry, max_concurrency=1
    ) as runtime:
        first = await runtime.create(_interval("first"))
        second = await runtime.create(_interval("second"))
        await asyncio.gather(
            runtime.run_now(first.task_id), runtime.run_now(second.task_id)
        )
        assert maximum == 1


@pytest.mark.asyncio
async def test_run_now_does_not_consume_once_schedule(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    registry.register("core.diagnostic", _noop)
    async with SchedulerRuntime.open(tmp_path / "scheduled.sqlite", registry) as runtime:
        task = await runtime.create(
            ScheduledTaskInput(
                name="once",
                handler_name="core.diagnostic",
                schedule_kind=ScheduleKind.ONCE,
                run_at=datetime.now(UTC) + timedelta(minutes=5),
            )
        )
        await runtime.run_now(task.task_id)
        assert runtime.scheduler.get_job(task.task_id) is not None
        assert (await runtime.list())[0].status is TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_invalid_update_preserves_existing_task(tmp_path: Path) -> None:
    registry = TaskHandlerRegistry()
    registry.register("core.diagnostic", _noop)
    async with SchedulerRuntime.open(tmp_path / "scheduled.sqlite", registry) as runtime:
        task = await runtime.create(_interval())
        invalid = _interval("changed").model_copy(
            update={
                "schedule_kind": ScheduleKind.CRON,
                "cron_expression": "invalid",
                "interval_seconds": None,
            }
        )
        with pytest.raises(ValueError, match="five fields"):
            await runtime.update(task.task_id, invalid)

        stored = (await runtime.list())[0]
        assert stored.name == "diagnostic"
        assert stored.schedule_kind is ScheduleKind.INTERVAL


async def _noop(context: TaskHandlerContext) -> None:
    return None
