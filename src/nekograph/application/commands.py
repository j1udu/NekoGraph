"""Deterministic command registration and routing before the agent runtime."""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from nekograph.application.ports import AgentRuntime
from nekograph.logging import fields
from nekograph.models import RunContext

logger = logging.getLogger(__name__)
_COMMAND_NAME = re.compile(r"^/[A-Za-z][A-Za-z0-9_-]{0,31}$")
CommandHandler = Callable[[RunContext, tuple[str, ...]], Awaitable[str]]


class CommandRegistrationError(ValueError):
    """A command cannot be registered in the application registry."""


@dataclass(frozen=True, slots=True)
class CommandDefinition:
    name: str
    description: str
    handler: CommandHandler
    source: str


class CommandRegistry:
    """Registry of deterministic commands, grouped by their registration owner."""

    def __init__(self) -> None:
        self._commands: dict[str, CommandDefinition] = {}
        self._reserved: set[str] = set()

    def reserve(self, name: str) -> None:
        if not _COMMAND_NAME.fullmatch(name):
            raise CommandRegistrationError(f"invalid command name: {name!r}; expected /name")
        self._reserved.add(name.casefold())

    def register(self, definition: CommandDefinition) -> None:
        if not _COMMAND_NAME.fullmatch(definition.name):
            raise CommandRegistrationError(
                f"invalid command name: {definition.name!r}; expected /name"
            )
        if not definition.description.strip():
            raise CommandRegistrationError(
                f"command description is empty: {definition.name}"
            )
        if not definition.source.strip():
            raise CommandRegistrationError(
                f"command source is empty: {definition.name}"
            )
        if definition.name.casefold() in self._reserved and definition.source != "core":
            raise CommandRegistrationError(f"command name is reserved: {definition.name}")
        if definition.name.casefold() in {
            name.casefold() for name in self._commands
        }:
            raise CommandRegistrationError(f"duplicate command name: {definition.name}")
        self._commands[definition.name] = definition

    def unregister_owner(self, source: str) -> None:
        """Remove all commands owned by ``source``; used for plugin rollback."""
        self._commands = {
            name: command
            for name, command in self._commands.items()
            if command.source != source
        }

    def definitions(self) -> tuple[CommandDefinition, ...]:
        return tuple(self._commands.values())

    def get(self, name: str) -> CommandDefinition | None:
        folded = name.casefold()
        return next(
            (
                command
                for command in self._commands.values()
                if command.name.casefold() == folded
            ),
            None,
        )


@dataclass(slots=True)
class CommandRouter:
    runtime: AgentRuntime
    registry: CommandRegistry

    async def dispatch(self, context: RunContext, text: str) -> str | None:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = tuple(stripped.split())
        command = self.registry.get(parts[0])
        if command is None:
            return (
                f"Unknown command: {parts[0].casefold()}. "
                "Use /help to list commands."
            )

        try:
            return await command.handler(context, parts[1:])
        except Exception:
            logger.exception(
                "command_handler_failed",
                extra=fields(
                    command=command.name,
                    source=command.source,
                    conversation_id=context.conversation.conversation_id,
                ),
            )
            return "Command failed to execute. Please try again later."


def register_core_commands(registry: CommandRegistry, runtime: AgentRuntime) -> None:
    async def help_command(_context: RunContext, _args: tuple[str, ...]) -> str:
        commands = ", ".join(command.name for command in registry.definitions())
        return f"Available commands: {commands}"

    async def status_command(_context: RunContext, _args: tuple[str, ...]) -> str:
        return "NekoGraph is running. Agent runtime: LangGraph. Checkpoint: SQLite."

    async def reset_command(context: RunContext, _args: tuple[str, ...]) -> str:
        await runtime.reset(context.conversation)
        return "Conversation context has been reset."

    async def approve_command(context: RunContext, args: tuple[str, ...]) -> str:
        if len(args) != 1:
            return "Usage: /approve <approval_id>"
        return await runtime.approve(context, args[0])

    async def deny_command(context: RunContext, args: tuple[str, ...]) -> str:
        if len(args) != 1:
            return "Usage: /deny <approval_id>"
        return await runtime.deny(context, args[0])

    for definition in (
        CommandDefinition("/help", "List available commands.", help_command, "core"),
        CommandDefinition("/status", "Show runtime status.", status_command, "core"),
        CommandDefinition("/reset", "Reset the current conversation.", reset_command, "core"),
        CommandDefinition("/approve", "Approve a pending tool request.", approve_command, "core"),
        CommandDefinition("/deny", "Deny a pending tool request.", deny_command, "core"),
    ):
        registry.register(definition)


def build_core_command_registry(runtime: AgentRuntime) -> CommandRegistry:
    registry = CommandRegistry()
    for name in ("/help", "/status", "/reset", "/approve", "/deny"):
        registry.reserve(name)
    register_core_commands(registry, runtime)
    return registry
