from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from nekograph.application.conversation import ConversationResolver
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.models import ConversationRef, RunContext
from nekograph.protocols.local_console import LocalConsoleAdapter


@dataclass
class ConsoleRuntime:
    responses: list[tuple[RunContext, str]] = field(default_factory=lambda: [])

    async def respond(self, context: RunContext, text: str) -> str:
        self.responses.append((context, text))
        return f"agent:{text}"

    async def reset(self, conversation: ConversationRef) -> None:
        return None

    async def approve(self, context: RunContext, approval_id: str) -> str:
        return f"approved:{approval_id}"

    async def deny(self, context: RunContext, approval_id: str) -> str:
        return f"denied:{approval_id}"


def scripted_reader(lines: list[str]) -> Callable[[str], Awaitable[str]]:
    values = iter(lines)

    async def read(prompt: str) -> str:
        assert prompt == "You> "
        return next(values)

    return read


async def test_console_routes_messages_and_commands_through_application() -> None:
    runtime = ConsoleRuntime()
    application = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(namespace="local:v1"),
        wakeup=WakeupPolicy(),
    )
    output: list[str] = []
    adapter = LocalConsoleAdapter(
        application,
        reader=scripted_reader(["hello", "/status", "/exit"]),
        writer=output.append,
    )

    await adapter.run()

    assert len(runtime.responses) == 1
    context, text = runtime.responses[0]
    assert text == "hello"
    assert context.conversation.conversation_id == (
        "local:v1:bot:local-agent:private:local-user"
    )
    assert output == [
        "NekoGraph local chat started. Type /exit or /quit to stop.",
        "NekoGraph> agent:hello",
        "NekoGraph> NekoGraph is running. Agent runtime: LangGraph. Checkpoint: SQLite.",
    ]


async def test_console_ignores_empty_input_and_stops_on_eof() -> None:
    runtime = ConsoleRuntime()
    application = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(namespace="local:v1"),
        wakeup=WakeupPolicy(),
    )
    calls = 0

    async def read(prompt: str) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            return "   "
        raise EOFError

    adapter = LocalConsoleAdapter(application, reader=read, writer=lambda _: None)

    await adapter.run()

    assert runtime.responses == []


def test_console_events_use_internal_models_and_unique_message_ids() -> None:
    runtime = ConsoleRuntime()
    application = MessageApplication(
        runtime=runtime,
        conversations=ConversationResolver(namespace="local:v1"),
        wakeup=WakeupPolicy(),
    )
    adapter = LocalConsoleAdapter(application)

    first = adapter.build_event("first")
    second = adapter.build_event("second")

    assert first.message.message_id == "local-1"
    assert second.message.message_id == "local-2"
    assert first.message.plain_text == "first"
    assert first.message.chat.chat_id == "local-user"
    assert "raw" not in first.model_dump()
