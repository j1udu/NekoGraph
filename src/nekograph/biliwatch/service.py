"""Deterministic polling, deduplication, retry, and OneBot delivery workflow."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Protocol

from nekograph.biliwatch.client import dynamic_type_label
from nekograph.biliwatch.config import BiliWatchConfigStore
from nekograph.biliwatch.models import (
    BiliDynamic,
    BiliLiveStatus,
    DeliveryStatus,
    PendingDelivery,
    PollReport,
    StoredContent,
    Subscription,
    SubscriptionInput,
    WatchType,
)
from nekograph.biliwatch.store import BiliWatchStore
from nekograph.logging import fields
from nekograph.models import Chat, ChatKind, MessageSegment, OutboundMessage
from nekograph.protocols.onebot_v11.actions import SendReceipt
from nekograph.scheduling import ScheduledTaskInput, ScheduleKind, SchedulerRuntime

logger = logging.getLogger(__name__)


class BilibiliGateway(Protocol):
    async def recent_dynamics(self, uid: str) -> list[BiliDynamic]: ...

    async def dynamic_detail(self, dynamic_id: str, uid: str) -> BiliDynamic | None: ...

    async def live_status(self, uid: str) -> BiliLiveStatus: ...

    async def creator_name(self, uid: str) -> str: ...

    async def test_cookie(self) -> bool: ...


class MessageSender(Protocol):
    async def send(
        self,
        message: OutboundMessage,
        *,
        source: str,
        correlation_id: str | None = None,
    ) -> SendReceipt: ...


class BiliWatchService:
    def __init__(
        self,
        store: BiliWatchStore,
        client: BilibiliGateway,
        sender: MessageSender,
        config: BiliWatchConfigStore,
    ) -> None:
        self.store = store
        self.client = client
        self.sender = sender
        self.config = config
        self._poll_lock = asyncio.Lock()
        self._scheduler: SchedulerRuntime | None = None
        self._last_cookie_check_at: datetime | None = None
        self._cookie_warned = False

    async def bind_scheduler(self, scheduler: SchedulerRuntime) -> None:
        self._scheduler = scheduler
        if await self.store.subscriptions():
            await self.sync_polling_schedule()

    async def sync_polling_schedule(self) -> None:
        scheduler = self._scheduler
        if scheduler is None:
            return
        tasks = [
            item
            for item in await scheduler.list()
            if item.handler_name == "biliwatch.poll"
        ]
        interval = self.config.current.poll_interval_seconds
        if not await self.store.subscriptions():
            for task in tasks:
                await scheduler.delete(task.task_id)
            return
        if not tasks:
            await scheduler.create(
                ScheduledTaskInput(
                    name="BiliWatch 轮询",
                    handler_name="biliwatch.poll",
                    schedule_kind=ScheduleKind.INTERVAL,
                    interval_seconds=interval,
                    timezone="Asia/Shanghai",
                    enabled=True,
                )
            )
            return
        primary, *duplicates = tasks
        for duplicate in duplicates:
            await scheduler.delete(duplicate.task_id)
        if primary.interval_seconds != interval or not primary.enabled:
            await scheduler.update(
                primary.task_id,
                ScheduledTaskInput(
                    name=primary.name,
                    handler_name=primary.handler_name,
                    schedule_kind=ScheduleKind.INTERVAL,
                    interval_seconds=interval,
                    timezone=primary.timezone,
                    payload=primary.payload,
                    enabled=True,
                ),
            )

    async def save_subscription(self, data: SubscriptionInput) -> Subscription:
        existing = await self.store.by_target(data.bot_id, data.group_id, data.uid)
        uname = existing.uname if existing is not None else data.uid
        baseline = existing.last_dynamic_timestamp if existing is not None else None
        try:
            uname = await self.client.creator_name(data.uid)
        except Exception:
            logger.warning("biliwatch_creator_lookup_failed", extra=fields(uid=data.uid))
        if existing is None and data.watch_dynamic:
            try:
                dynamics = await self.client.recent_dynamics(data.uid)
                baseline = max(
                    (int(item.published_at.timestamp()) for item in dynamics), default=0
                )
                if dynamics:
                    uname = dynamics[0].uname
            except Exception:
                logger.warning("biliwatch_baseline_failed", extra=fields(uid=data.uid))
        saved = await self.store.save_subscription(
            data, uname=uname, last_dynamic_timestamp=baseline
        )
        await self.sync_polling_schedule()
        return saved

    async def delete_subscription(self, subscription_id: str) -> bool:
        deleted = await self.store.delete_subscription(subscription_id)
        if deleted:
            await self.sync_polling_schedule()
        return deleted

    async def poll(self) -> PollReport:
        async with self._poll_lock:
            retried, retry_sent, retry_failed = await self._retry_pending()
            all_subscriptions = await self.store.subscriptions()
            await self._check_cookie_validity(
                {item.bot_id for item in all_subscriptions}
            )
            subscriptions = [item for item in all_subscriptions if item.enabled]
            by_uid: dict[str, list[Subscription]] = defaultdict(list)
            for subscription in subscriptions:
                by_uid[subscription.uid].append(subscription)

            discovered = 0
            sent = retry_sent
            failed = retry_failed
            for uid, targets in by_uid.items():
                if any(item.watch_dynamic for item in targets):
                    try:
                        dynamics = await self.client.recent_dynamics(uid)
                        values = await self._process_dynamics(targets, dynamics)
                        discovered += values[0]
                        sent += values[1]
                        failed += values[2]
                    except Exception as exc:
                        logger.warning(
                            "biliwatch_dynamic_poll_failed",
                            extra=fields(uid=uid, error=str(exc)),
                        )
                if any(item.watch_live for item in targets):
                    try:
                        live = await self.client.live_status(uid)
                        values = await self._process_live(targets, live)
                        discovered += values[0]
                        sent += values[1]
                        failed += values[2]
                    except Exception as exc:
                        logger.warning(
                            "biliwatch_live_poll_failed",
                            extra=fields(uid=uid, error=str(exc)),
                        )
            return PollReport(
                checked_uids=len(by_uid),
                discovered_contents=discovered,
                sent_deliveries=sent,
                failed_deliveries=failed,
                retried_deliveries=retried,
            )

    async def _check_cookie_validity(self, bot_ids: set[str]) -> None:
        """Periodically check the configured cookie without interrupting polling."""
        if not bot_ids or not self.config.current.cookie_header:
            return
        now = datetime.now(UTC)
        if self._last_cookie_check_at is not None and (
            now - self._last_cookie_check_at < timedelta(minutes=30)
        ):
            return
        self._last_cookie_check_at = now
        try:
            if not await self.client.test_cookie():
                raise RuntimeError("Bilibili cookie check returned false")
        except Exception as exc:
            if self._cookie_warned:
                return
            self._cookie_warned = True
            logger.warning(
                "biliwatch_cookie_invalid",
                extra=fields(error=str(exc)),
            )
            await self._notify_admins(
                bot_ids,
                "BiliWatch：B 站 Cookie 可能已失效，动态接口调用失败，请及时更新配置。",
            )
        else:
            if self._cookie_warned:
                logger.info("biliwatch_cookie_recovered")
            self._cookie_warned = False

    async def _notify_admins(self, bot_ids: set[str], content: str) -> None:
        for bot_id in bot_ids:
            for admin in self.config.current.admins:
                try:
                    await self.sender.send(
                        OutboundMessage.text(
                            bot_id=bot_id,
                            chat=Chat(kind=ChatKind.PRIVATE, chat_id=admin),
                            content=content,
                        ),
                        source="biliwatch",
                        correlation_id=f"cookie-warning:{bot_id}:{admin}",
                    )
                except Exception as exc:
                    logger.warning(
                        "biliwatch_cookie_notification_failed",
                        extra=fields(bot_id=bot_id, admin_id=admin, error=str(exc)),
                    )

    async def _process_dynamics(
        self, targets: list[Subscription], dynamics: list[BiliDynamic]
    ) -> tuple[int, int, int]:
        if not dynamics:
            return (0, 0, 0)
        newest_timestamp = max(int(item.published_at.timestamp()) for item in dynamics)
        newest_name = max(dynamics, key=lambda item: item.published_at).uname
        discovered = 0
        sent = 0
        failed = 0
        for target in targets:
            if not target.watch_dynamic:
                continue
            cursor = target.last_dynamic_timestamp
            if cursor is None:
                await self.store.update_dynamic_cursor(
                    target.subscription_id, newest_timestamp, uname=newest_name
                )
                continue
            candidates = sorted(
                (
                    item
                    for item in dynamics
                    if int(item.published_at.timestamp()) > cursor
                    and not (
                        target.filter_forward
                        and item.dynamic_type == "DYNAMIC_TYPE_FORWARD"
                    )
                ),
                key=lambda item: item.published_at,
            )
            for dynamic in candidates:
                if not dynamic.text:
                    detail = await self.client.dynamic_detail(
                        dynamic.dynamic_id, dynamic.uid
                    )
                    if detail is not None:
                        dynamic = detail
                created, delivery_sent = await self._queue_and_send(
                    target, _dynamic_content(dynamic)
                )
                discovered += int(created)
                sent += int(delivery_sent)
                failed += int(not delivery_sent)
            await self.store.update_dynamic_cursor(
                target.subscription_id, newest_timestamp, uname=newest_name
            )
        return discovered, sent, failed

    async def _process_live(
        self, targets: list[Subscription], live: BiliLiveStatus
    ) -> tuple[int, int, int]:
        discovered = 0
        sent = 0
        failed = 0
        uname: str | None = None
        for target in targets:
            if not target.watch_live:
                continue
            if live.is_live and not target.was_live:
                if uname is None:
                    try:
                        uname = await self.client.creator_name(target.uid)
                    except Exception:
                        uname = target.uname
                content = _live_content(live, uname)
                created, delivery_sent = await self._queue_and_send(target, content)
                discovered += int(created)
                sent += int(delivery_sent)
                failed += int(not delivery_sent)
            await self.store.update_live_state(
                target.subscription_id, live.is_live, uname=uname
            )
        return discovered, sent, failed

    async def _queue_and_send(
        self, subscription: Subscription, content: StoredContent
    ) -> tuple[bool, bool]:
        created = await self.store.save_content(content)
        delivery = await self.store.ensure_delivery(
            subscription.subscription_id, content.content_key
        )
        if delivery.status is DeliveryStatus.SENT:
            return created, True
        pending = PendingDelivery(
            delivery=delivery,
            subscription=subscription,
            content=content,
        )
        return created, await self._send(pending)

    async def _retry_pending(self) -> tuple[int, int, int]:
        pending = await self.store.pending_deliveries()
        sent = 0
        failed = 0
        for item in pending:
            if await self._send(item):
                sent += 1
            else:
                failed += 1
        return len(pending), sent, failed

    async def _send(self, item: PendingDelivery) -> bool:
        try:
            receipt = await self.sender.send(
                _outbound(item),
                source="biliwatch",
                correlation_id=item.delivery.delivery_id,
            )
            await self.store.mark_delivery_sent(
                item.delivery.delivery_id, receipt.message_id
            )
            return True
        except Exception as exc:
            await self.store.mark_delivery_failed(
                item.delivery.delivery_id, str(exc)
            )
            logger.warning(
                "biliwatch_delivery_failed",
                extra=fields(
                    delivery_id=item.delivery.delivery_id,
                    group_id=item.subscription.group_id,
                    error=str(exc),
                ),
            )
            return False


def _dynamic_content(dynamic: BiliDynamic) -> StoredContent:
    return StoredContent(
        content_key=dynamic.content_key,
        uid=dynamic.uid,
        kind=WatchType.DYNAMIC,
        published_at=dynamic.published_at,
        payload=dynamic.model_dump(mode="json"),
        discovered_at=datetime.now(UTC),
    )


def _live_content(live: BiliLiveStatus, uname: str) -> StoredContent:
    observed = datetime.now(UTC)
    room = live.room_id or live.uid
    return StoredContent(
        content_key=f"live:{live.uid}:{room}:{observed.isoformat()}",
        uid=live.uid,
        kind=WatchType.LIVE,
        published_at=observed,
        payload={**live.model_dump(mode="json"), "uname": uname},
        discovered_at=observed,
    )


def _outbound(item: PendingDelivery) -> OutboundMessage:
    subscription = item.subscription
    content = item.content
    at_all = (
        subscription.at_all_dynamic
        if content.kind is WatchType.DYNAMIC
        else subscription.at_all_live
    )
    segments: list[MessageSegment] = []
    if at_all:
        segments.append(MessageSegment(kind="at", data={"qq": "all"}))
    if content.kind is WatchType.DYNAMIC:
        dynamic = BiliDynamic.model_validate(content.payload)
        segments.append(MessageSegment.text(_format_dynamic(dynamic, at_all=at_all)))
        segments.extend(
            MessageSegment(kind="image", data={"file": url})
            for url in dynamic.image_urls
            if url
        )
        segments.append(MessageSegment.text(f"\n{dynamic.url}"))
    else:
        live = BiliLiveStatus.model_validate(content.payload)
        uname = str(content.payload.get("uname") or subscription.uname)
        segments.append(MessageSegment.text(_format_live(live, uname, at_all=at_all)))
        if live.cover_url:
            segments.append(MessageSegment(kind="image", data={"file": live.cover_url}))
        if live.url:
            segments.append(MessageSegment.text(f"\n{live.url}"))
    return OutboundMessage(
        bot_id=subscription.bot_id,
        chat=Chat(kind=ChatKind.GROUP, chat_id=subscription.group_id),
        segments=tuple(segments),
    )


def _format_dynamic(dynamic: BiliDynamic, *, at_all: bool) -> str:
    text = dynamic.text[:200] + ("..." if len(dynamic.text) > 200 else "")
    prefix = "\n" if at_all else ""
    message = f"{prefix}[BiliWatch] {dynamic.uname} {dynamic_type_label(dynamic.dynamic_type)}"
    if text:
        message += f"\n{text}"
    if dynamic.original_uname:
        original = (dynamic.original_text or "")[:150]
        message += f"\n原动态: {dynamic.original_uname} {dynamic.original_type or ''}"
        if original:
            message += f"\n{original}"
    return message


def _format_live(live: BiliLiveStatus, uname: str, *, at_all: bool) -> str:
    prefix = "\n" if at_all else ""
    title = f"\n{live.title}" if live.title else ""
    return f"{prefix}[BiliWatch] {uname} 开播了{title}"
