from __future__ import annotations

from datetime import UTC, datetime

from nekograph.application.events import EventDisposition, EventRouter
from nekograph.models import OneBotEvent, OneBotNoticeEvent, UnknownOneBotEvent


def _unknown() -> UnknownOneBotEvent:
    return UnknownOneBotEvent(
        bot_id="10000",
        timestamp=datetime.now(UTC),
        post_type="unknown",
        event_type="extension",
    )


async def test_event_router_orders_handlers_and_stops_when_consumed() -> None:
    router = EventRouter()
    calls: list[str] = []

    async def low(event: OneBotEvent) -> EventDisposition:
        calls.append("low")
        return EventDisposition.CONTINUE

    async def high(event: OneBotEvent) -> EventDisposition:
        calls.append("high")
        return EventDisposition.CONSUMED

    router.register(UnknownOneBotEvent, low, priority=0)
    router.register(UnknownOneBotEvent, high, priority=100)

    result = await router.dispatch(_unknown())

    assert result is EventDisposition.CONSUMED
    assert calls == ["high"]


async def test_event_router_filters_types_and_isolates_handler_failure() -> None:
    router = EventRouter()
    calls: list[str] = []

    async def notice_only(event: OneBotEvent) -> None:
        calls.append("notice")

    async def broken(event: OneBotEvent) -> None:
        calls.append("broken")
        raise RuntimeError("isolated")

    async def healthy(event: OneBotEvent) -> None:
        calls.append("healthy")

    router.register(OneBotNoticeEvent, notice_only, priority=100)
    router.register(UnknownOneBotEvent, broken, priority=50)
    router.register(UnknownOneBotEvent, healthy)

    result = await router.dispatch(_unknown())

    assert result is EventDisposition.CONTINUE
    assert calls == ["broken", "healthy"]
