from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import BaseModel, ConfigDict

from nekograph.application.commands import CommandRegistry
from nekograph.models import RunContext
from nekograph.plugins import PluginContext, PluginManager, PluginMetadata
from nekograph.tools import JsonValue, ToolExecutionContext, ToolRegistry, ToolRisk


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def echo(arguments: BaseModel) -> JsonValue:
    parsed = EchoArgs.model_validate(arguments)
    return {"echo": parsed.text}


@dataclass
class EchoPlugin:
    metadata = PluginMetadata(name="echo", version="0.1.0")

    async def setup(self, context: PluginContext) -> None:
        async def command(_context: RunContext, args: tuple[str, ...]) -> str:
            return " ".join(args)

        context.commands.register(
            name="/echo",
            description="Echo command",
            handler=command,
        )
        context.tools.register(
            name="echo_text",
            description="Echo text through the agent tool registry.",
            args_schema=EchoArgs,
            handler=echo,
            risk=ToolRisk.SAFE,
        )

    async def shutdown(self) -> None:
        return None


@dataclass
class BrokenPlugin:
    metadata = PluginMetadata(name="broken", version="0.1.0")

    async def setup(self, context: PluginContext) -> None:
        async def command(_context: RunContext, _args: tuple[str, ...]) -> str:
            return "never"

        context.commands.register(
            name="/broken",
            description="Broken command",
            handler=command,
        )
        context.tools.register(
            name="broken_tool",
            description="Broken tool.",
            args_schema=EchoArgs,
            handler=echo,
        )
        raise RuntimeError("setup failed")

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_plugin_tool_and_command_register_with_owner() -> None:
    tools = ToolRegistry()
    commands = CommandRegistry()
    manager = PluginManager(tool_registry=tools, command_registry=commands)

    status = await manager.load(EchoPlugin())

    assert status.loaded is True
    assert commands.get("/echo") is not None
    assert tools.definitions()[0].source == "plugin:echo"
    assert tools.model_specs()[0]["function"]["name"] == "echo_text"
    result = await tools.execute(
        name="echo_text",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
    )
    assert result.output == {"echo": "hello"}


@pytest.mark.asyncio
async def test_failed_plugin_setup_rolls_back_command_and_tool() -> None:
    tools = ToolRegistry()
    commands = CommandRegistry()
    manager = PluginManager(tool_registry=tools, command_registry=commands)

    status = await manager.load(BrokenPlugin())

    assert status.loaded is False
    assert status.error == "RuntimeError"
    assert commands.get("/broken") is None
    assert tools.definitions() == ()
