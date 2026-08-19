"""Stable metadata and results for framework tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type ToolHandler = Callable[[BaseModel], Awaitable[JsonValue]]


class ToolRisk(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    DANGEROUS = "dangerous"


class ToolResultCode(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    INVALID_ARGUMENTS = "invalid_arguments"
    PERMISSION_DENIED = "permission_denied"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_DENIED = "approval_denied"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_INVALID = "approval_invalid"
    DANGEROUS_DISABLED = "dangerous_disabled"
    TIMEOUT = "timeout"
    HANDLER_ERROR = "handler_error"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    args_schema: type[BaseModel]
    handler: ToolHandler
    source: str
    risk: ToolRisk = ToolRisk.SAFE
    timeout_seconds: float = 10.0
    required_permissions: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    permissions: frozenset[str] = frozenset()
    allow_dangerous: bool = False


@dataclass(frozen=True, slots=True)
class PreparedTool:
    definition: ToolDefinition
    arguments: BaseModel


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_name: str
    code: ToolResultCode
    output: JsonValue = None
    error: str | None = None

    @property
    def success(self) -> bool:
        return self.code is ToolResultCode.SUCCESS


@dataclass(frozen=True, slots=True)
class ToolPreparation:
    prepared: PreparedTool | None = None
    result: ToolResult | None = None

    def __post_init__(self) -> None:
        if (self.prepared is None) == (self.result is None):
            raise ValueError("exactly one of prepared or result is required")
