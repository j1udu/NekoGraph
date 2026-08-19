from __future__ import annotations

from dataclasses import dataclass

import pytest

from nekograph.application.commands import (
    CommandDefinition,
    CommandRegistrationError,
    CommandRegistry,
    CommandRouter,
    build_core_command_registry,
)
from nekograph.models import Actor, Chat, ChatKind, ConversationRef, RunContext


@dataclass
class Runtime:
    reset_count: int = 0

    async def respond(self, context: RunContext, text: str) -> str:
        return f"agent:{text}"

    async def reset(self, conversation: ConversationRef) -> None:
        self.reset_count += 1

    async def approve(self, context: RunContext, approval_id: str) -> str:
        return f"approved:{approval_id}"

    async def deny(self, context: RunContext, approval_id: str) -> str:
        return f"denied:{approval_id}"


def context() -> RunContext:
    return RunContext(
        run_id="run-1",
        bot_id="bot-1",
        actor=Actor(user_id="user-1"),
        chat=Chat(kind=ChatKind.PRIVATE, chat_id="chat-1"),
        conversation=ConversationRef(
            conversation_id="conversation-1", thread_id="thread-1"
        ),
    )


def test_registry_rejects_invalid_and_duplicate_names() -> None:
    registry = CommandRegistry()
    async def handler(_context: RunContext, _args: tuple[str, ...]) -> str:
        return "ok"

    with pytest.raises(CommandRegistrationError):
        registry.register(CommandDefinition("help", "invalid", handler, "plugin:test"))
    with pytest.raises(CommandRegistrationError):
        registry.register(CommandDefinition("/x", "", handler, "plugin:test"))

    registry.register(CommandDefinition("/x", "first", handler, "plugin:first"))
    with pytest.raises(CommandRegistrationError):
        registry.register(CommandDefinition("/X", "duplicate", handler, "plugin:second"))


def test_registry_can_remove_one_owner_without_touching_core() -> None:
    runtime = Runtime()
    registry = build_core_command_registry(runtime)
    async def plugin_command(_context: RunContext, _args: tuple[str, ...]) -> str:
        return "plugin"

    registry.register(CommandDefinition("/plugin", "plugin command", plugin_command, "plugin:test"))
    registry.unregister_owner("plugin:test")

    assert registry.get("/plugin") is None
    assert registry.get("/help") is not None


@pytest.mark.asyncio
async def test_plugin_command_is_deterministic_and_does_not_call_agent() -> None:
    runtime = Runtime()
    registry = build_core_command_registry(runtime)
    async def plugin_command(_context: RunContext, args: tuple[str, ...]) -> str:
        return f"plugin:{','.join(args)}"

    registry.register(CommandDefinition("/echo", "echo args", plugin_command, "plugin:test"))
    router = CommandRouter(runtime, registry)

    assert await router.dispatch(context(), "/echo one two") == "plugin:one,two"


@pytest.mark.asyncio
async def test_plugin_command_exception_is_isolated() -> None:
    runtime = Runtime()
    registry = build_core_command_registry(runtime)

    async def broken(_context: RunContext, _args: tuple[str, ...]) -> str:
        raise RuntimeError("plugin failure")

    registry.register(CommandDefinition("/broken", "broken", broken, "plugin:test"))
    router = CommandRouter(runtime, registry)

    assert await router.dispatch(context(), "/broken") == (
        "Command failed to execute. Please try again later."
    )
