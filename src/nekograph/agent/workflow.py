"""LangGraph nodes for model routing, tool policy, approval, and execution."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal, cast
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from nekograph.agent.ledger import ToolExecutionLedger
from nekograph.agent.model import ChatModel
from nekograph.agent.state import AgentState, ApprovalDecision, PendingApproval, PendingTool
from nekograph.tools import (
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
    ToolResultCode,
    ToolRisk,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _tool_result_content(result: ToolResult) -> str:
    return json.dumps(
        {
            "success": result.success,
            "code": result.code,
            "output": result.output,
            "error": result.error,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _tool_message(tool_call_id: str, tool_name: str, result: ToolResult) -> ToolMessage:
    return ToolMessage(
        content=_tool_result_content(result),
        tool_call_id=tool_call_id,
        name=tool_name,
        status="success" if result.success else "error",
    )


class AgentWorkflow:
    def __init__(
        self,
        *,
        model: ChatModel,
        saver: AsyncSqliteSaver,
        ledger: ToolExecutionLedger,
        tools: ToolRegistry,
        tool_context: ToolExecutionContext,
        approval_ttl_seconds: float,
        clock: Callable[[], datetime],
        approval_id_factory: Callable[[], str] | None,
    ) -> None:
        if approval_ttl_seconds <= 0:
            raise ValueError("approval_ttl_seconds must be greater than zero")
        self._model = model
        self._ledger = ledger
        self._tools = tools
        self._tool_context = tool_context
        self._approval_ttl = timedelta(seconds=approval_ttl_seconds)
        self._clock = clock
        self._approval_id_factory = approval_id_factory or (lambda: uuid4().hex)

        builder = StateGraph(AgentState)
        builder.add_node("model", self._call_model)
        builder.add_node("guard", self._guard_tool_call)
        builder.add_node("approval", self._await_approval)
        builder.add_node("execute", self._execute_pending_tool)
        builder.add_edge(START, "model")
        builder.add_conditional_edges("model", self._route_model)
        builder.add_conditional_edges("guard", self._route_guard)
        builder.add_conditional_edges("approval", self._route_approval)
        builder.add_edge("execute", "model")
        self.graph: CompiledStateGraph[AgentState, None, AgentState, AgentState] = (
            builder.compile(checkpointer=saver, name="nekograph-single-agent")
        )

    async def _call_model(self, state: AgentState) -> dict[str, object]:
        response = await self._model.complete(state["messages"], self._tools.model_specs())
        return {"messages": [response]}

    @staticmethod
    def _route_model(state: AgentState) -> Literal["guard", "__end__"]:
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "guard"
        return "__end__"

    async def _guard_tool_call(self, state: AgentState) -> dict[str, object]:
        last_message = state["messages"][-1]
        if not isinstance(last_message, AIMessage):
            raise RuntimeError("tool guard requires an AIMessage")
        if len(last_message.tool_calls) != 1:
            messages = [
                _tool_message(
                    str(tool_call.get("id") or "missing-tool-call-id"),
                    tool_call["name"],
                    ToolResult(
                        tool_name=tool_call["name"],
                        code=ToolResultCode.INVALID_ARGUMENTS,
                        error="NekoGraph executes one tool call at a time; retry separately",
                    ),
                )
                for tool_call in last_message.tool_calls
            ]
            return {
                "messages": messages,
                "pending_tool": None,
                "pending_approval": None,
                "route": "model",
            }

        tool_call = last_message.tool_calls[0]
        tool_call_id = tool_call.get("id")
        if not tool_call_id:
            raise RuntimeError("model returned a tool call without an ID")
        preparation = self._tools.prepare(
            name=tool_call["name"],
            arguments=tool_call["args"],
            context=self._tool_context,
        )
        if preparation.result is not None:
            return {
                "messages": [
                    _tool_message(tool_call_id, tool_call["name"], preparation.result)
                ],
                "pending_tool": None,
                "pending_approval": None,
                "route": "model",
            }
        prepared = preparation.prepared
        assert prepared is not None
        arguments = prepared.arguments.model_dump(mode="json")
        risk = prepared.definition.risk
        approval_id = None if risk is ToolRisk.SAFE else self._approval_id_factory()
        pending_tool: PendingTool = {
            "tool_call_id": tool_call_id,
            "name": prepared.definition.name,
            "arguments": arguments,
            "risk": risk,
            "approval_id": approval_id,
        }
        if risk is ToolRisk.SAFE:
            return {
                "pending_tool": pending_tool,
                "pending_approval": None,
                "route": "execute",
            }

        actor_id = state.get("actor_id")
        conversation_id = state.get("conversation_id")
        if not actor_id or not conversation_id or approval_id is None:
            raise RuntimeError("approval requires internal actor and conversation identity")
        created_at = self._clock()
        pending_approval: PendingApproval = {
            "approval_id": approval_id,
            "tool_call_id": tool_call_id,
            "tool_name": prepared.definition.name,
            "arguments": arguments,
            "risk": risk,
            "actor_id": actor_id,
            "conversation_id": conversation_id,
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + self._approval_ttl).isoformat(),
        }
        return {
            "pending_tool": pending_tool,
            "pending_approval": pending_approval,
            "route": "approval",
        }

    @staticmethod
    def _route_guard(state: AgentState) -> Literal["model", "execute", "approval"]:
        route = state.get("route")
        if route not in {"model", "execute", "approval"}:
            raise RuntimeError("tool guard produced an invalid route")
        return route

    async def _await_approval(self, state: AgentState) -> dict[str, object]:
        pending = state.get("pending_approval")
        if pending is None:
            raise RuntimeError("approval node requires a pending approval")
        raw_decision = interrupt(
            {
                "approval_id": pending["approval_id"],
                "tool_name": pending["tool_name"],
                "arguments": pending["arguments"],
                "risk": pending["risk"],
                "expires_at": pending["expires_at"],
            }
        )
        decision = cast(ApprovalDecision, raw_decision)
        valid_identity = (
            decision.get("approval_id") == pending["approval_id"]
            and decision.get("actor_id") == pending["actor_id"]
            and decision.get("conversation_id") == pending["conversation_id"]
        )
        expired = self.is_expired(pending)
        if valid_identity and decision.get("approved") is True and not expired:
            return {"pending_approval": None, "route": "execute"}

        if expired:
            code = ToolResultCode.APPROVAL_EXPIRED
            error = "tool approval expired"
        elif not valid_identity:
            code = ToolResultCode.APPROVAL_INVALID
            error = "tool approval identity is invalid"
        else:
            code = ToolResultCode.APPROVAL_DENIED
            error = "tool execution was denied"
        result = ToolResult(tool_name=pending["tool_name"], code=code, error=error)
        return {
            "messages": [
                _tool_message(pending["tool_call_id"], pending["tool_name"], result)
            ],
            "pending_tool": None,
            "pending_approval": None,
            "route": "model",
        }

    @staticmethod
    def _route_approval(state: AgentState) -> Literal["model", "execute"]:
        route = state.get("route")
        if route == "model":
            return "model"
        if route == "execute":
            return "execute"
        raise RuntimeError("approval node produced an invalid route")

    async def _execute_pending_tool(self, state: AgentState) -> dict[str, object]:
        pending = state.get("pending_tool")
        if pending is None:
            raise RuntimeError("tool executor requires a pending tool")
        risk = ToolRisk(pending["risk"])
        if risk is ToolRisk.SAFE:
            result = await self._tools.execute(
                name=pending["name"],
                arguments=pending["arguments"],
                context=self._tool_context,
            )
        else:
            result = await self._execute_approved_tool(state, pending)
        return {
            "messages": [
                _tool_message(pending["tool_call_id"], pending["name"], result)
            ],
            "pending_tool": None,
            "pending_approval": None,
            "route": "model",
        }

    async def _execute_approved_tool(
        self, state: AgentState, pending: PendingTool
    ) -> ToolResult:
        approval_id = pending["approval_id"]
        conversation_id = state.get("conversation_id")
        if approval_id is None or conversation_id is None:
            raise RuntimeError("approved tool is missing durable identity")
        claimed = await self._ledger.claim(
            approval_id=approval_id,
            conversation_id=conversation_id,
            tool_name=pending["name"],
        )
        if claimed:
            result = await self._tools.execute(
                name=pending["name"],
                arguments=pending["arguments"],
                context=self._tool_context,
                approved=True,
            )
            await self._ledger.complete(approval_id, result)
            return result

        record = await self._ledger.get(approval_id)
        if (
            record is not None
            and record.conversation_id == conversation_id
            and record.tool_name == pending["name"]
            and record.result is not None
        ):
            return record.result
        if record is not None and (
            record.conversation_id != conversation_id or record.tool_name != pending["name"]
        ):
            return ToolResult(
                tool_name=pending["name"],
                code=ToolResultCode.APPROVAL_INVALID,
                error="approval execution identity is invalid",
            )
        return ToolResult(
            tool_name=pending["name"],
            code=ToolResultCode.HANDLER_ERROR,
            error="tool execution was already claimed; result unavailable",
        )

    def is_expired(self, pending: PendingApproval) -> bool:
        return self._clock() >= datetime.fromisoformat(pending["expires_at"])

    @staticmethod
    def approval_prompt(pending: PendingApproval) -> str:
        arguments = json.dumps(pending["arguments"], ensure_ascii=False, separators=(",", ":"))
        if len(arguments) > 500:
            arguments = f"{arguments[:497]}..."
        return (
            f"Tool approval required [{pending['risk']}]: {pending['tool_name']} {arguments}\n"
            f"/approve {pending['approval_id']}\n"
            f"/deny {pending['approval_id']}"
        )
