"""Small core tools used to demonstrate safe and approval-gated execution."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path, PurePath
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nekograph.tools.models import JsonValue, ToolDefinition, ToolRisk
from nekograph.tools.registry import ToolRegistry


class CurrentTimeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "UTC"


class WriteDemoFileArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=100_000)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        candidate = PurePath(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("path must remain inside the demo sandbox")
        return value


async def _get_current_time(arguments: BaseModel) -> JsonValue:
    parsed = CurrentTimeArgs.model_validate(arguments)
    try:
        timezone = ZoneInfo(parsed.timezone)
    except ZoneInfoNotFoundError:
        return {"error": "unknown timezone"}
    return {
        "timezone": parsed.timezone,
        "iso8601": datetime.now(timezone).isoformat(),
    }


def _write_demo_file_handler(sandbox: Path):
    root = sandbox.resolve()

    async def write(arguments: BaseModel) -> JsonValue:
        parsed = WriteDemoFileArgs.model_validate(arguments)
        destination = (root / parsed.path).resolve()
        if not destination.is_relative_to(root):
            raise ValueError("path escaped the demo sandbox")

        def persist() -> None:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(parsed.content, encoding="utf-8")

        await asyncio.to_thread(persist)
        return {"path": destination.relative_to(root).as_posix(), "bytes": len(parsed.content)}

    return write


def build_core_tool_registry(sandbox: Path) -> ToolRegistry:
    return ToolRegistry(
        (
            ToolDefinition(
                name="get_current_time",
                description="Return the current time in an IANA timezone.",
                args_schema=CurrentTimeArgs,
                handler=_get_current_time,
                source="core",
                risk=ToolRisk.SAFE,
                timeout_seconds=2.0,
            ),
            ToolDefinition(
                name="write_demo_file",
                description="Write text to a relative path in the NekoGraph demo sandbox.",
                args_schema=WriteDemoFileArgs,
                handler=_write_demo_file_handler(sandbox),
                source="core",
                risk=ToolRisk.SENSITIVE,
                timeout_seconds=5.0,
                required_permissions=frozenset({"demo_file:write"}),
            ),
        )
    )
