"""Stable models for task definitions and execution history."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScheduleKind(StrEnum):
    CRON = "cron"
    INTERVAL = "interval"
    ONCE = "once"


class TaskStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class ScheduledTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)
    handler_name: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.-]+$")
    schedule_kind: ScheduleKind
    cron_expression: str | None = Field(default=None, max_length=120)
    interval_seconds: int | None = Field(default=None, gt=0, le=31_536_000)
    run_at: datetime | None = None
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduledTask(ScheduledTaskInput):
    task_id: str
    status: TaskStatus
    last_run_at: datetime | None = None
    next_run_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class TaskRun(BaseModel):
    run_id: str
    task_id: str
    scheduled_at: datetime
    started_at: datetime
    finished_at: datetime | None = None
    status: TaskStatus
    error: str | None = None
    duration_ms: int | None = None
