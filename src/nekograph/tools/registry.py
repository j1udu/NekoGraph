"""Tool registration, policy checks, validation, and failure isolation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Iterable

from pydantic import ValidationError

from nekograph.logging import fields
from nekograph.model_types import ModelToolSpec
from nekograph.tools.models import (
    PreparedTool,
    ToolDefinition,
    ToolExecutionContext,
    ToolPreparation,
    ToolResult,
    ToolResultCode,
    ToolRisk,
)

logger = logging.getLogger(__name__)
_TOOL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")


class ToolRegistrationError(ValueError):
    """A tool definition can't be added to the registry."""


class ToolRegistry:
    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._definitions: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if not _TOOL_NAME.fullmatch(definition.name):
            raise ToolRegistrationError(f"invalid tool name: {definition.name!r}")
        if definition.name in self._definitions:
            raise ToolRegistrationError(f"duplicate tool name: {definition.name}")
        if not definition.description.strip():
            raise ToolRegistrationError(f"tool description is empty: {definition.name}")
        if definition.timeout_seconds <= 0:
            raise ToolRegistrationError(f"tool timeout must be positive: {definition.name}")
        self._definitions[definition.name] = definition

    def model_specs(self) -> tuple[ModelToolSpec, ...]:
        return tuple(
            {
                "type": "function",
                "function": {
                    "name": definition.name,
                    "description": definition.description,
                    "parameters": definition.args_schema.model_json_schema(),
                },
            }
            for definition in self._definitions.values()
        )

    def prepare(
        self,
        *,
        name: str,
        arguments: object,
        context: ToolExecutionContext,
    ) -> ToolPreparation:
        definition = self._definitions.get(name)
        if definition is None:
            return self._failure(name, ToolResultCode.NOT_FOUND, "tool is not registered")
        try:
            validated = definition.args_schema.model_validate(arguments)
        except ValidationError:
            return self._failure(
                name,
                ToolResultCode.INVALID_ARGUMENTS,
                "tool arguments failed validation",
                definition,
            )
        missing = definition.required_permissions - context.permissions
        if missing:
            return self._failure(
                name,
                ToolResultCode.PERMISSION_DENIED,
                "required tool permission is missing",
                definition,
            )
        if definition.risk is ToolRisk.DANGEROUS and not context.allow_dangerous:
            return self._failure(
                name,
                ToolResultCode.DANGEROUS_DISABLED,
                "dangerous tools are disabled",
                definition,
            )
        return ToolPreparation(prepared=PreparedTool(definition=definition, arguments=validated))

    async def execute(
        self,
        *,
        name: str,
        arguments: object,
        context: ToolExecutionContext,
        approved: bool = False,
    ) -> ToolResult:
        preparation = self.prepare(name=name, arguments=arguments, context=context)
        if preparation.result is not None:
            return preparation.result
        prepared = preparation.prepared
        assert prepared is not None
        definition = prepared.definition
        if definition.risk is not ToolRisk.SAFE and not approved:
            logger.info(
                "tool_approval_required",
                extra=fields(
                    tool_name=name,
                    source=definition.source,
                    risk=definition.risk,
                    code=ToolResultCode.APPROVAL_REQUIRED,
                ),
            )
            return ToolResult(
                tool_name=name,
                code=ToolResultCode.APPROVAL_REQUIRED,
                error="tool requires explicit approval",
            )

        try:
            output = await asyncio.wait_for(
                definition.handler(prepared.arguments),
                timeout=definition.timeout_seconds,
            )
            json.dumps(output, ensure_ascii=False)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                "tool_execution_timed_out",
                extra=fields(
                    tool_name=name,
                    source=definition.source,
                    risk=definition.risk,
                    code=ToolResultCode.TIMEOUT,
                ),
            )
            return ToolResult(
                tool_name=name,
                code=ToolResultCode.TIMEOUT,
                error="tool execution timed out",
            )
        except Exception as exc:
            logger.error(
                "tool_execution_failed",
                extra=fields(
                    tool_name=name,
                    source=definition.source,
                    risk=definition.risk,
                    code=ToolResultCode.HANDLER_ERROR,
                    exception_type=type(exc).__name__,
                ),
            )
            return ToolResult(
                tool_name=name,
                code=ToolResultCode.HANDLER_ERROR,
                error="tool execution failed",
            )

        logger.info(
            "tool_execution_succeeded",
            extra=fields(
                tool_name=name,
                source=definition.source,
                risk=definition.risk,
                code=ToolResultCode.SUCCESS,
            ),
        )
        return ToolResult(tool_name=name, code=ToolResultCode.SUCCESS, output=output)

    @staticmethod
    def _failure(
        name: str,
        code: ToolResultCode,
        error: str,
        definition: ToolDefinition | None = None,
    ) -> ToolPreparation:
        logger.warning(
            "tool_request_rejected",
            extra=fields(
                tool_name=name,
                source=definition.source if definition is not None else "unknown",
                risk=definition.risk if definition is not None else None,
                code=code,
            ),
        )
        return ToolPreparation(result=ToolResult(tool_name=name, code=code, error=error))
