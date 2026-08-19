from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import cast

import pytest
from pydantic import BaseModel, ConfigDict

from nekograph.tools import (
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistrationError,
    ToolRegistry,
    ToolResultCode,
    ToolRisk,
    build_core_tool_registry,
)
from nekograph.tools.models import JsonValue


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def echo(arguments: BaseModel) -> JsonValue:
    parsed = EchoArgs.model_validate(arguments)
    return {"echo": parsed.text}


def definition(**overrides: object) -> ToolDefinition:
    values: dict[str, object] = {
        "name": "echo",
        "description": "Echo text.",
        "args_schema": EchoArgs,
        "handler": echo,
        "source": "test",
        "risk": ToolRisk.SAFE,
        "timeout_seconds": 1.0,
        "required_permissions": frozenset(),
    }
    values.update(overrides)
    return ToolDefinition(**values)  # type: ignore[arg-type]


def test_registry_rejects_duplicate_names() -> None:
    registry = ToolRegistry((definition(),))

    with pytest.raises(ToolRegistrationError, match="duplicate"):
        registry.register(definition())


def test_registry_exports_model_schema_without_handler() -> None:
    registry = ToolRegistry((definition(),))

    specs = registry.model_specs()

    assert specs[0]["function"]["name"] == "echo"
    assert specs[0]["function"]["parameters"]["additionalProperties"] is False
    assert "handler" not in repr(specs)


async def test_registry_validates_arguments() -> None:
    registry = ToolRegistry((definition(),))

    result = await registry.execute(
        name="echo",
        arguments={"wrong": "value"},
        context=ToolExecutionContext(),
    )

    assert result.code is ToolResultCode.INVALID_ARGUMENTS
    assert "wrong" not in (result.error or "")


async def test_registry_rejects_missing_permissions() -> None:
    registry = ToolRegistry(
        (definition(required_permissions=frozenset({"echo:use"})),)
    )

    result = await registry.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
    )

    assert result.code is ToolResultCode.PERMISSION_DENIED


async def test_registry_times_out_handler() -> None:
    async def slow(arguments: BaseModel) -> JsonValue:
        await asyncio.sleep(0.1)
        return "late"

    registry = ToolRegistry((definition(handler=slow, timeout_seconds=0.001),))

    result = await registry.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
    )

    assert result.code is ToolResultCode.TIMEOUT


async def test_registry_hides_handler_exception() -> None:
    async def fail(arguments: BaseModel) -> JsonValue:
        raise RuntimeError("private handler detail")

    registry = ToolRegistry((definition(handler=fail),))

    result = await registry.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
    )

    assert result.code is ToolResultCode.HANDLER_ERROR
    assert "private handler detail" not in (result.error or "")


async def test_registry_structured_logs_hide_arguments_and_exception_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(arguments: BaseModel) -> JsonValue:
        raise RuntimeError("private handler detail")

    registry = ToolRegistry((definition(handler=fail),))
    caplog.set_level(logging.INFO, logger="nekograph.tools.registry")

    invalid = await registry.execute(
        name="echo",
        arguments={"wrong": "private argument"},
        context=ToolExecutionContext(),
    )
    failed = await registry.execute(
        name="echo",
        arguments={"text": "private argument"},
        context=ToolExecutionContext(),
    )

    assert invalid.code is ToolResultCode.INVALID_ARGUMENTS
    assert failed.code is ToolResultCode.HANDLER_ERROR
    assert [record.getMessage() for record in caplog.records] == [
        "tool_request_rejected",
        "tool_execution_failed",
    ]
    assert "private argument" not in caplog.text
    assert "private handler detail" not in caplog.text
    raw_fields = getattr(caplog.records[-1], "event_fields", None)
    assert isinstance(raw_fields, dict)
    failure_fields = cast(dict[str, object], raw_fields)
    assert failure_fields["code"] is ToolResultCode.HANDLER_ERROR
    assert failure_fields["exception_type"] == "RuntimeError"


async def test_sensitive_and_dangerous_tools_enforce_risk_policy() -> None:
    sensitive = ToolRegistry((definition(risk=ToolRisk.SENSITIVE),))
    dangerous = ToolRegistry((definition(risk=ToolRisk.DANGEROUS),))

    needs_approval = await sensitive.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
    )
    disabled = await dangerous.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(),
        approved=True,
    )
    approved = await dangerous.execute(
        name="echo",
        arguments={"text": "hello"},
        context=ToolExecutionContext(allow_dangerous=True),
        approved=True,
    )

    assert needs_approval.code is ToolResultCode.APPROVAL_REQUIRED
    assert disabled.code is ToolResultCode.DANGEROUS_DISABLED
    assert approved.code is ToolResultCode.SUCCESS


async def test_write_demo_file_is_sandboxed_and_requires_approval(tmp_path: Path) -> None:
    registry = build_core_tool_registry(tmp_path / "sandbox")
    context = ToolExecutionContext(permissions=frozenset({"demo_file:write"}))

    traversal = await registry.execute(
        name="write_demo_file",
        arguments={"path": "../outside.txt", "content": "no"},
        context=context,
        approved=True,
    )
    waiting = await registry.execute(
        name="write_demo_file",
        arguments={"path": "notes/demo.txt", "content": "hello"},
        context=context,
    )
    written = await registry.execute(
        name="write_demo_file",
        arguments={"path": "notes/demo.txt", "content": "hello"},
        context=context,
        approved=True,
    )

    assert traversal.code is ToolResultCode.INVALID_ARGUMENTS
    assert waiting.code is ToolResultCode.APPROVAL_REQUIRED
    assert written.code is ToolResultCode.SUCCESS
    assert (tmp_path / "sandbox" / "notes" / "demo.txt").read_text() == "hello"
    assert not (tmp_path / "outside.txt").exists()


async def test_get_current_time_executes_as_safe_tool(tmp_path: Path) -> None:
    registry = build_core_tool_registry(tmp_path / "sandbox")

    result = await registry.execute(
        name="get_current_time",
        arguments={"timezone": "UTC"},
        context=ToolExecutionContext(),
    )

    assert result.code is ToolResultCode.SUCCESS
    assert isinstance(result.output, dict)
    assert result.output["timezone"] == "UTC"
    assert isinstance(result.output["iso8601"], str)
    assert result.output["iso8601"].endswith("+00:00")
