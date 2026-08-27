from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import httpx
from pydantic import SecretStr

from nekograph.application.commands import CommandRegistry, CommandRouter
from nekograph.biliwatch.client import BilibiliClient, parse_dynamic
from nekograph.biliwatch.commands import register_biliwatch_commands
from nekograph.biliwatch.config import (
    BiliWatchConfig,
    BiliWatchConfigStore,
    BiliWatchConfigUpdate,
)
from nekograph.biliwatch.models import (
    BiliDynamic,
    BiliLiveStatus,
    DeliveryStatus,
    SubscriptionInput,
)
from nekograph.biliwatch.service import BilibiliGateway, BiliWatchService, MessageSender
from nekograph.biliwatch.store import BiliWatchStore
from nekograph.models import (
    Actor,
    Chat,
    ChatKind,
    ConversationRef,
    OutboundMessage,
    RunContext,
)
from nekograph.protocols.onebot_v11.actions import SendReceipt
from nekograph.scheduling import SchedulerRuntime, TaskHandlerRegistry


def _dynamic(dynamic_id: str, *, seconds: int, text: str = "new post") -> BiliDynamic:
    return BiliDynamic(
        dynamic_id=dynamic_id,
        uid="123",
        uname="Test UP",
        dynamic_type="DYNAMIC_TYPE_DRAW",
        published_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds),
        text=text,
        image_urls=("https://example.com/image.jpg",),
        url=f"https://t.bilibili.com/{dynamic_id}",
    )


class FakeBilibili:
    def __init__(self) -> None:
        self.dynamics: list[BiliDynamic] = []
        self.live = BiliLiveStatus(uid="123", live_status=0)
        self.dynamic_calls = 0
        self.live_calls = 0
        self.cookie_calls = 0
        self.cookie_error: Exception | None = None

    async def recent_dynamics(self, uid: str) -> list[BiliDynamic]:
        self.dynamic_calls += 1
        return self.dynamics

    async def dynamic_detail(self, dynamic_id: str, uid: str) -> BiliDynamic | None:
        return next((item for item in self.dynamics if item.dynamic_id == dynamic_id), None)

    async def live_status(self, uid: str) -> BiliLiveStatus:
        self.live_calls += 1
        return self.live

    async def creator_name(self, uid: str) -> str:
        return "Test UP"

    async def test_cookie(self) -> bool:
        self.cookie_calls += 1
        if self.cookie_error is not None:
            raise self.cookie_error
        return True


class FakeSender:
    def __init__(self) -> None:
        self.messages: list[OutboundMessage] = []
        self.failures = 0

    async def send(
        self,
        message: OutboundMessage,
        *,
        source: str,
        correlation_id: str | None = None,
    ) -> SendReceipt:
        assert source == "biliwatch"
        assert correlation_id
        if self.failures:
            self.failures -= 1
            raise ConnectionError("QQ unavailable")
        self.messages.append(message)
        return SendReceipt(
            bot_id=message.bot_id,
            message_id=str(len(self.messages)),
            chat_kind=message.chat.kind,
            chat_id=message.chat.chat_id,
            sent_at=datetime.now(UTC),
        )


async def _config(tmp_path: Path, *, admins: tuple[str, ...] = ()) -> BiliWatchConfigStore:
    return await BiliWatchConfigStore.open(
        tmp_path / "biliwatch-config.json", BiliWatchConfig(admins=admins)
    )


async def test_bilibili_client_parses_dynamic_and_uses_configured_cookie(
    tmp_path: Path,
) -> None:
    seen_cookie = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_cookie
        seen_cookie = request.headers.get("cookie", "")
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "items": [
                        {
                            "id_str": "9001",
                            "type": "DYNAMIC_TYPE_AV",
                            "modules": {
                                "module_author": {
                                    "name": "Test UP",
                                    "pub_ts": 1767225600,
                                },
                                "module_dynamic": {
                                    "major": {
                                        "archive": {
                                            "title": "New video",
                                            "cover": "https://example.com/cover.jpg",
                                        }
                                    }
                                },
                            },
                        }
                    ]
                },
            },
        )

    config = await BiliWatchConfigStore.open(
        tmp_path / "config.json",
        BiliWatchConfig(sessdata=SecretStr("cookie-value")),
    )
    client = BilibiliClient(config, transport=httpx.MockTransport(handler))
    try:
        dynamics = await client.recent_dynamics("123")
    finally:
        await client.aclose()

    assert seen_cookie == "SESSDATA=cookie-value"
    assert len(dynamics) == 1
    assert dynamics[0].dynamic_id == "9001"
    assert dynamics[0].text == "New video"
    assert dynamics[0].image_urls == ("https://example.com/cover.jpg",)


def test_parse_dynamic_supports_opus_and_forwarded_content() -> None:
    original = {
        "id_str": "100",
        "type": "DYNAMIC_TYPE_DRAW",
        "modules": {
            "module_author": {"name": "Original UP", "pub_ts": 100},
            "module_dynamic": {
                "major": {
                    "type": "MAJOR_TYPE_OPUS",
                    "opus": {
                        "title": "Opus title",
                        "summary": {"rich_text_nodes": [{"orig_text": "body"}]},
                        "pics": [{"url": "https://example.com/opus.jpg"}],
                    },
                }
            },
        },
    }
    forwarded = {
        "id_str": "101",
        "type": "DYNAMIC_TYPE_FORWARD",
        "modules": {
            "module_author": {"name": "Forwarder", "pub_ts": 101},
            "module_dynamic": {"desc": {"text": "comment"}},
        },
        "orig": original,
    }

    parsed = parse_dynamic(forwarded, "123")

    assert parsed is not None
    assert parsed.text == "comment"
    assert parsed.original_uname == "Original UP"
    assert parsed.original_text == "Opus title\nbody"
    assert parsed.image_urls == ("https://example.com/opus.jpg",)


async def test_subscription_and_delivery_state_survive_store_restart(tmp_path: Path) -> None:
    path = tmp_path / "biliwatch.sqlite"
    async with BiliWatchStore.open(path) as store:
        saved = await store.save_subscription(
            SubscriptionInput(bot_id="10000", group_id="30001", uid="123"),
            uname="Test UP",
            last_dynamic_timestamp=100,
        )
        await store.update_live_state(saved.subscription_id, True)

    async with BiliWatchStore.open(path) as reopened:
        subscriptions = await reopened.subscriptions()

    assert len(subscriptions) == 1
    assert subscriptions[0].last_dynamic_timestamp == 100
    assert subscriptions[0].was_live


async def test_poll_uses_one_request_per_uid_and_delivers_to_each_group(tmp_path: Path) -> None:
    client = FakeBilibili()
    sender = FakeSender()
    config = await _config(tmp_path)
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        client.dynamics = [_dynamic("old", seconds=10)]
        await service.save_subscription(
            SubscriptionInput(
                bot_id="10000", group_id="30001", uid="123", watch_live=False
            )
        )
        await service.save_subscription(
            SubscriptionInput(
                bot_id="10000",
                group_id="30002",
                uid="123",
                watch_live=False,
                at_all_dynamic=True,
            )
        )
        client.dynamic_calls = 0
        client.dynamics = [_dynamic("new", seconds=20), _dynamic("old", seconds=10)]

        report = await service.poll()
        deliveries = await store.deliveries()

    assert client.dynamic_calls == 1
    assert report.discovered_contents == 1
    assert report.sent_deliveries == 2
    assert {message.chat.chat_id for message in sender.messages} == {"30001", "30002"}
    assert len(deliveries) == 2
    assert all(item.status is DeliveryStatus.SENT for item in deliveries)
    mentioned = next(item for item in sender.messages if item.chat.chat_id == "30002")
    assert mentioned.segments[0].kind == "at"
    assert mentioned.segments[0].data == {"qq": "all"}


async def test_failed_delivery_retries_after_cursor_advances(tmp_path: Path) -> None:
    client = FakeBilibili()
    sender = FakeSender()
    config = await _config(tmp_path)
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        client.dynamics = [_dynamic("old", seconds=10)]
        await service.save_subscription(
            SubscriptionInput(
                bot_id="10000", group_id="30001", uid="123", watch_live=False
            )
        )
        client.dynamics = [_dynamic("new", seconds=20)]
        sender.failures = 1

        first = await service.poll()
        second = await service.poll()
        deliveries = await store.deliveries()

    assert first.failed_deliveries == 1
    assert second.retried_deliveries == 1
    assert second.sent_deliveries == 1
    assert len(sender.messages) == 1
    assert deliveries[0].status is DeliveryStatus.SENT
    assert deliveries[0].attempts == 2


async def test_live_push_only_occurs_on_offline_to_live_transition(tmp_path: Path) -> None:
    client = FakeBilibili()
    sender = FakeSender()
    config = await _config(tmp_path)
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        await service.save_subscription(
            SubscriptionInput(
                bot_id="10000",
                group_id="30001",
                uid="123",
                watch_dynamic=False,
                watch_live=True,
            )
        )
        client.live = BiliLiveStatus(
            uid="123",
            live_status=1,
            room_id="900",
            title="Live now",
            url="https://live.bilibili.com/900",
        )
        await service.poll()
        await service.poll()
        client.live = client.live.model_copy(update={"live_status": 0})
        await service.poll()
        client.live = client.live.model_copy(update={"live_status": 1})
        await service.poll()

    assert len(sender.messages) == 2


async def test_subscription_owns_one_persistent_polling_task(tmp_path: Path) -> None:
    client = FakeBilibili()
    sender = FakeSender()
    config = await _config(tmp_path)
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        registry = TaskHandlerRegistry()
        async def poll_handler(context: object) -> None:
            del context
        registry.register("biliwatch.poll", poll_handler)
        async with SchedulerRuntime.open(tmp_path / "tasks.sqlite", registry) as scheduler:
            await service.bind_scheduler(scheduler)
            assert await scheduler.list() == []
            saved = await service.save_subscription(
                SubscriptionInput(
                    bot_id="10000", group_id="30001", uid="123", watch_live=False
                )
            )
            tasks = await scheduler.list()
            assert len(tasks) == 1
            assert tasks[0].handler_name == "biliwatch.poll"
            await config.update(BiliWatchConfigUpdate(admins=(), poll_interval_seconds=120))
            await service.sync_polling_schedule()
            assert (await scheduler.list())[0].interval_seconds == 120
            await service.delete_subscription(saved.subscription_id)
            assert await scheduler.list() == []


class SpyRuntime:
    def __init__(self) -> None:
        self.responses = 0

    async def respond(self, context: RunContext, text: str) -> str:
        self.responses += 1
        return "agent"

    async def reset(self, conversation: ConversationRef) -> None:
        return None

    async def approve(self, context: RunContext, approval_id: str) -> str:
        return "approved"

    async def deny(self, context: RunContext, approval_id: str) -> str:
        return "denied"


def _run_context(user_id: str = "20001", *, group: bool = False) -> RunContext:
    return RunContext(
        run_id="run-1",
        bot_id="10000",
        actor=Actor(user_id=user_id),
        chat=Chat(
            kind=ChatKind.GROUP if group else ChatKind.PRIVATE,
            chat_id="30001" if group else user_id,
        ),
        conversation=ConversationRef(conversation_id="c", thread_id="c"),
    )


async def test_watch_command_is_admin_private_and_never_calls_agent(tmp_path: Path) -> None:
    client = FakeBilibili()
    sender = FakeSender()
    config = await _config(tmp_path, admins=("20001",))
    runtime = SpyRuntime()
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        registry = CommandRegistry()
        register_biliwatch_commands(registry, service, config)
        router = CommandRouter(runtime, registry)

        denied = await router.dispatch(
            _run_context("20002"), "/watch -g 30001 -w 动态 123"
        )
        group_denied = await router.dispatch(
            _run_context(group=True), "/watch -g 30001 -w 动态 123"
        )
        allowed = await router.dispatch(
            _run_context(), "/watch -g 30001 -w 动态 123"
        )

    assert denied == "你没有管理 BiliWatch 的权限。"
    assert group_denied == "BiliWatch 管理命令只能由管理员私聊使用。"
    assert allowed is not None and "已订阅" in allowed
    assert runtime.responses == 0


async def test_legacy_command_aliases_and_preview_commands_stay_deterministic(
    tmp_path: Path,
) -> None:
    client = FakeBilibili()
    client.dynamics = [_dynamic("9001", seconds=10, text="preview text")]
    client.live = BiliLiveStatus(
        uid="123",
        live_status=1,
        room_id="456",
        title="preview live",
        url="https://live.bilibili.com/456",
    )
    sender = FakeSender()
    config = await _config(tmp_path, admins=("20001",))
    runtime = SpyRuntime()
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        registry = CommandRegistry()
        register_biliwatch_commands(registry, service, config)
        router = CommandRouter(runtime, registry)
        context = _run_context()

        help_text = await router.dispatch(context, "/watchhelp")
        interval_text = await router.dispatch(context, "/interval 120")
        preview_text = await router.dispatch(
            context, "/watchtest https://t.bilibili.com/9001"
        )
        live_text = await router.dispatch(context, "/livetest 123")
        aliases = await router.dispatch(context, "/list")

    assert help_text is not None and "/watch" in help_text
    assert interval_text == "BiliWatch 轮询间隔已设置为 120 秒。"
    assert config.current.poll_interval_seconds == 120
    assert preview_text is not None and "preview text" in preview_text
    assert live_text is not None and "preview live" in live_text
    assert aliases == "当前没有符合条件的 BiliWatch 订阅。"
    assert runtime.responses == 0


async def test_cookie_failure_notifies_each_admin_once_and_does_not_break_poll(
    tmp_path: Path,
) -> None:
    client = FakeBilibili()
    client.cookie_error = ConnectionError("expired")
    sender = FakeSender()
    config = await BiliWatchConfigStore.open(
        tmp_path / "config.json",
        BiliWatchConfig(admins=("20001", "20002"), sessdata=SecretStr("cookie")),
    )
    async with BiliWatchStore.open(tmp_path / "store.sqlite") as store:
        service = BiliWatchService(
            store,
            cast(BilibiliGateway, client),
            cast(MessageSender, sender),
            config,
        )
        await service.save_subscription(
            SubscriptionInput(
                bot_id="10000",
                group_id="30001",
                uid="123",
                watch_dynamic=False,
                watch_live=False,
            )
        )
        await service.poll()
        await service.poll()

    assert client.cookie_calls == 1
    notifications = [
        message
        for message in sender.messages
        if message.chat.kind is ChatKind.PRIVATE
    ]
    assert {message.chat.chat_id for message in notifications} == {"20001", "20002"}
