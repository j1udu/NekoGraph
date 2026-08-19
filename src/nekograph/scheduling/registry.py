"""Registry of safe, named handlers executed by scheduled tasks."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from nekograph.scheduling.models import ScheduledTask

TaskHandler = Callable[["TaskHandlerContext"], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class TaskHandlerContext:
    """The narrow context passed to a scheduled business handler."""

    run_id: str
    task: ScheduledTask
    payload: Mapping[str, Any]


class TaskHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: dict[str, TaskHandler] = {}

    def register(self, name: str, handler: TaskHandler) -> None:
        if not name or name.strip() != name:
            raise ValueError("handler name must be a non-empty trimmed string")
        if name in self._handlers:
            raise ValueError(f"task handler already registered: {name}")
        self._handlers[name] = handler

    def get(self, name: str) -> TaskHandler | None:
        return self._handlers.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._handlers))
