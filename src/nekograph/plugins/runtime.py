"""Plugin lifecycle and controlled Command/Tool registration."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel

from nekograph.application.commands import (
    CommandDefinition,
    CommandHandler,
    CommandRegistry,
)
from nekograph.logging import fields
from nekograph.tools import JsonValue, ToolDefinition, ToolRegistry, ToolRisk

logger = logging.getLogger(__name__)
PluginToolHandler = Callable[[BaseModel], Awaitable[JsonValue]]


def _new_statuses() -> list[PluginStatus]:
    return []


def _new_plugins() -> dict[str, Plugin]:
    return {}


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str


class Plugin(Protocol):
    metadata: PluginMetadata

    async def setup(self, context: PluginContext) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PluginStatus:
    metadata: PluginMetadata
    loaded: bool
    error: str | None = None


@dataclass(slots=True)
class PluginContext:
    """Capabilities exposed to one plugin; no Application or WebSocket access."""

    metadata: PluginMetadata
    commands: _PluginCommandRegistrar
    tools: _PluginToolRegistrar


@dataclass(slots=True)
class _PluginCommandRegistrar:
    registry: CommandRegistry
    source: str

    def register(
        self,
        *,
        name: str,
        description: str,
        handler: CommandHandler,
    ) -> None:
        self.registry.register(
            CommandDefinition(name, description, handler, self.source)
        )


@dataclass(slots=True)
class _PluginToolRegistrar:
    registry: ToolRegistry
    source: str

    def register(
        self,
        *,
        name: str,
        description: str,
        args_schema: type[BaseModel],
        handler: PluginToolHandler,
        risk: ToolRisk = ToolRisk.SAFE,
        timeout_seconds: float = 10.0,
        required_permissions: frozenset[str] = frozenset(),
    ) -> None:
        self.registry.register(
            ToolDefinition(
                name=name,
                description=description,
                args_schema=args_schema,
                handler=handler,
                source=self.source,
                risk=risk,
                timeout_seconds=timeout_seconds,
                required_permissions=required_permissions,
            )
        )


@dataclass(slots=True)
class PluginManager:
    """Load explicitly supplied local plugins with per-plugin rollback."""

    tool_registry: ToolRegistry
    command_registry: CommandRegistry
    _statuses: list[PluginStatus] = field(default_factory=_new_statuses, init=False)
    _plugins: dict[str, Plugin] = field(default_factory=_new_plugins, init=False)

    async def load_all(self, plugins: Iterable[Plugin]) -> None:
        for plugin in plugins:
            await self.load(plugin)

    async def load(self, plugin: Plugin) -> PluginStatus:
        metadata = plugin.metadata
        source = f"plugin:{metadata.name}"
        if metadata.name in self._plugins:
            status = PluginStatus(metadata, False, "duplicate plugin name")
            self._statuses.append(status)
            return status

        context = PluginContext(
            metadata=metadata,
            commands=_PluginCommandRegistrar(self.command_registry, source),
            tools=_PluginToolRegistrar(self.tool_registry, source),
        )
        try:
            await plugin.setup(context)
        except Exception as exc:
            self.command_registry.unregister_owner(source)
            self.tool_registry.unregister_owner(source)
            logger.exception(
                "plugin_setup_failed",
                extra=fields(plugin=metadata.name, version=metadata.version),
            )
            status = PluginStatus(metadata, False, type(exc).__name__)
            self._statuses.append(status)
            return status

        self._plugins[metadata.name] = plugin
        status = PluginStatus(metadata, True)
        self._statuses.append(status)
        logger.info(
            "plugin_loaded",
            extra=fields(plugin=metadata.name, version=metadata.version),
        )
        return status

    async def shutdown(self) -> None:
        for name, plugin in reversed(tuple(self._plugins.items())):
            try:
                await plugin.shutdown()
            except Exception:
                logger.exception("plugin_shutdown_failed", extra=fields(plugin=name))

    def statuses(self) -> tuple[PluginStatus, ...]:
        return tuple(self._statuses)
