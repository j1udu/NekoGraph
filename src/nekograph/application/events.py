"""Typed internal event routing before command and Agent execution."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from nekograph.logging import fields
from nekograph.models import OneBotEvent

logger = logging.getLogger(__name__)


class EventDisposition(StrEnum):
    CONTINUE = "continue"
    CONSUMED = "consumed"


EventHandler = Callable[[OneBotEvent], Awaitable[EventDisposition | None]]


@dataclass(frozen=True, slots=True)
class EventHandlerDefinition:
    event_type: type[object]
    handler: EventHandler
    priority: int
    name: str


class EventRouter:
    def __init__(self) -> None:
        self._handlers: list[EventHandlerDefinition] = []

    def register(
        self,
        event_type: type[object],
        handler: EventHandler,
        *,
        priority: int = 0,
        name: str | None = None,
    ) -> None:
        definition = EventHandlerDefinition(
            event_type=event_type,
            handler=handler,
            priority=priority,
            name=name or getattr(handler, "__name__", type(handler).__name__),
        )
        self._handlers.append(definition)
        self._handlers.sort(key=lambda item: item.priority, reverse=True)

    async def dispatch(self, event: OneBotEvent) -> EventDisposition:
        for definition in self._handlers:
            if not isinstance(event, definition.event_type):
                continue
            try:
                result = await definition.handler(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "event_handler_failed",
                    extra=fields(
                        handler=definition.name,
                        event_type=type(event).__name__,
                    ),
                )
                continue
            if result is EventDisposition.CONSUMED:
                return EventDisposition.CONSUMED
        return EventDisposition.CONTINUE

    def definitions(self) -> tuple[EventHandlerDefinition, ...]:
        return tuple(self._handlers)
