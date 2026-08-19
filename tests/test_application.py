from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field

import pytest

from nekograph.application.conversation import ConversationResolver
from nekograph.application.scheduler import ConversationScheduler
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.models import (
    ConversationRef,
    GroupConversationMode,
    InboundMessageEvent,
    Message,
    MessageSegment,
    RunContext,
)


@dataclass
class SpyRuntime:
    responses: int = 0
    resets: int = 0
    approvals: int = 0
    denials: int = 0
    received_texts: list[str] = field(default_factory=lambda: [])

    async def respond(self, context: RunContext, text: str) -> str:
        self.responses += 1
        self.received_texts.append(text)
        return f"agent:{text}"

    async def reset(self, conversation: ConversationRef) -> None:
        self.resets += 1

    async def approve(self, context: RunContext, approval_id: str) -> str:
        self.approvals += 1
        return f"approved:{approval_id}"

    async def deny(self, context: RunContext, approval_id: str) -> str:
        self.denials += 1
        return f"denied:{approval_id}"


def with_text(event: InboundMessageEvent, text: str) -> InboundMessageEvent:
    message = event.message.model_copy(
        update={"segments": (MessageSegment.text(text),), "plain_text": text}
    )
    assert isinstance(message, Message)
    return event.model_copy(update={"message": message})


def response_text(response_segments: tuple[MessageSegment, ...]) -> str:
    return "".join(segment.text_content for segment in response_segments)


def test_conversation_boundaries_are_explicit(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    private = event_factory("private_message.json")
    group = event_factory("group_message.json")

    private_ref = ConversationResolver().resolve(private)
    per_user = ConversationResolver(GroupConversationMode.PER_USER).resolve(group)
    shared = ConversationResolver(GroupConversationMode.SHARED).resolve(group)

    assert private_ref.conversation_id == "qq:v1:bot:10000:private:20001"
    assert per_user.conversation_id == "qq:v1:bot:10000:group:30001:user:20002"
    assert shared.conversation_id == "qq:v1:bot:10000:group:30001"
    assert per_user.thread_id == per_user.conversation_id
    assert per_user != shared


def test_group_users_share_only_when_configured(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    first = event_factory("group_message.json")
    second_message = first.message.model_copy(
        update={"actor": first.message.actor.model_copy(update={"user_id": "20003"})}
    )
    second = first.model_copy(update={"message": second_message})

    per_user = ConversationResolver(GroupConversationMode.PER_USER)
    shared = ConversationResolver(GroupConversationMode.SHARED)

    assert per_user.resolve(first) != per_user.resolve(second)
    assert shared.resolve(first) == shared.resolve(second)


def test_group_wakeup_supports_mentions_prefixes_and_direct_commands(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    group = event_factory("group_message.json")
    policy = WakeupPolicy(prefixes=("neko",))

    mention = policy.evaluate(group)
    prefix = policy.evaluate(with_text(group, "neko: explain checkpoints"))
    command = policy.evaluate(with_text(group, "/status"))
    ignored = policy.evaluate(with_text(group, "ordinary group chatter"))

    assert (mention.awake, mention.text, mention.reason) == (
        True,
        "explain LangGraph",
        "mention",
    )
    assert (prefix.awake, prefix.text) == (True, "explain checkpoints")
    assert (command.awake, command.reason) == (True, "command")
    assert not ignored.awake


@pytest.mark.parametrize(
    "command",
    [
        "/help",
        "/status",
        "/reset",
        "/approve approval-1",
        "/deny approval-1",
        "/unknown",
    ],
)
async def test_commands_never_call_agent(
    event_factory: Callable[[str], InboundMessageEvent], command: str
) -> None:
    runtime = SpyRuntime()
    app = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(),
    )

    response = await app.handle(with_text(event_factory("group_message.json"), command))

    assert response is not None
    assert runtime.responses == 0
    assert runtime.resets == (1 if command == "/reset" else 0)
    assert runtime.approvals == (1 if command.startswith("/approve ") else 0)
    assert runtime.denials == (1 if command.startswith("/deny ") else 0)


@pytest.mark.parametrize("command", ["/approve", "/approve one two", "/deny", "/deny one two"])
async def test_invalid_approval_command_does_not_resume_runtime(
    event_factory: Callable[[str], InboundMessageEvent], command: str
) -> None:
    runtime = SpyRuntime()
    app = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(),
    )

    response = await app.handle(with_text(event_factory("group_message.json"), command))

    assert response is not None
    assert runtime.responses == 0
    assert runtime.approvals == 0
    assert runtime.denials == 0


async def test_normal_message_reaches_agent_after_prefix_is_removed(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    runtime = SpyRuntime()
    app = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(prefixes=("neko",)),
    )

    response = await app.handle(
        with_text(event_factory("group_message.json"), "neko explain checkpointing")
    )

    assert response is not None
    assert runtime.received_texts == ["explain checkpointing"]
    assert response_text(response.segments) == "agent:explain checkpointing"


async def test_unwoken_group_message_is_ignored(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    runtime = SpyRuntime()
    app = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(),
    )

    response = await app.handle(
        with_text(event_factory("group_message.json"), "ordinary group chatter")
    )

    assert response is None
    assert runtime.responses == 0


async def test_agent_failure_is_isolated_to_current_message(
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    class FailingOnceRuntime(SpyRuntime):
        async def respond(self, context: RunContext, text: str) -> str:
            self.responses += 1
            if self.responses == 1:
                raise RuntimeError("model unavailable")
            return "recovered"

    runtime = FailingOnceRuntime()
    app = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(),
    )
    private = event_factory("private_message.json")

    failed = await app.handle(private)
    recovered = await app.handle(private)

    assert failed is not None
    assert recovered is not None
    assert "failed to process" in response_text(failed.segments)
    assert response_text(recovered.segments) == "recovered"


async def test_scheduler_serializes_same_conversation_and_allows_parallel_ones() -> None:
    scheduler = ConversationScheduler()
    same_active = 0
    same_peak = 0
    both_started = asyncio.Event()
    parallel_started: set[str] = set()

    async def same_operation() -> None:
        nonlocal same_active, same_peak
        same_active += 1
        same_peak = max(same_peak, same_active)
        await asyncio.sleep(0.02)
        same_active -= 1

    async def parallel_operation(name: str) -> None:
        parallel_started.add(name)
        if len(parallel_started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)

    await asyncio.gather(
        scheduler.run("same", same_operation),
        scheduler.run("same", same_operation),
    )
    await asyncio.gather(
        scheduler.run("conversation-a", lambda: parallel_operation("a")),
        scheduler.run("conversation-b", lambda: parallel_operation("b")),
    )

    assert same_peak == 1
    assert parallel_started == {"a", "b"}


async def test_scheduler_preserves_arrival_order() -> None:
    scheduler = ConversationScheduler()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[str] = []

    async def operation(name: str) -> None:
        order.append(name)
        if name == "first":
            first_started.set()
            await release_first.wait()

    first = asyncio.create_task(scheduler.run("same", lambda: operation("first")))
    await first_started.wait()
    second = asyncio.create_task(scheduler.run("same", lambda: operation("second")))
    await asyncio.sleep(0)
    release_first.set()
    await asyncio.gather(first, second)

    assert order == ["first", "second"]
