"""Checkpoint-safe LangGraph state owned by the agent runtime."""

# pyright: reportMissingTypeStubs=false

from __future__ import annotations

from typing import Any, Literal, TypedDict

from langgraph.graph import MessagesState

type ToolRoute = Literal["model", "execute", "approval"]


class PendingTool(TypedDict):
    tool_call_id: str
    name: str
    arguments: dict[str, Any]
    risk: str
    approval_id: str | None


class PendingApproval(TypedDict):
    approval_id: str
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    risk: str
    actor_id: str
    conversation_id: str
    created_at: str
    expires_at: str


class ApprovalDecision(TypedDict):
    approval_id: str
    approved: bool
    actor_id: str
    conversation_id: str
    reason: str


class AgentState(MessagesState, total=False):
    actor_id: str
    conversation_id: str
    pending_tool: PendingTool | None
    pending_approval: PendingApproval | None
    route: ToolRoute
