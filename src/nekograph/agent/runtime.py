"""Agent runtime port and persistent resource lifecycle."""

# pyright: reportMissingTypeStubs=false, reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import cast

import aiosqlite
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from nekograph.agent.ledger import ToolExecutionLedger
from nekograph.agent.model import ChatModel
from nekograph.agent.state import AgentState, ApprovalDecision, PendingApproval
from nekograph.agent.workflow import AgentWorkflow, utc_now
from nekograph.models import ConversationRef, RunContext
from nekograph.tools import ToolExecutionContext, ToolRegistry


class LangGraphRuntime:
    def __init__(
        self,
        *,
        model: ChatModel,
        saver: AsyncSqliteSaver,
        ledger: ToolExecutionLedger,
        tools: ToolRegistry | None = None,
        tool_context: ToolExecutionContext | None = None,
        approval_ttl_seconds: float = 900,
        clock: Callable[[], datetime] = utc_now,
        approval_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._saver = saver
        self._ledger = ledger
        self._workflow = AgentWorkflow(
            model=model,
            saver=saver,
            ledger=ledger,
            tools=tools or ToolRegistry(),
            tool_context=tool_context or ToolExecutionContext(),
            approval_ttl_seconds=approval_ttl_seconds,
            clock=clock,
            approval_id_factory=approval_id_factory,
        )
        self._graph = self._workflow.graph

    @classmethod
    @asynccontextmanager
    async def open(
        cls,
        *,
        checkpoint_path: Path,
        model: ChatModel,
        tools: ToolRegistry | None = None,
        tool_context: ToolExecutionContext | None = None,
        execution_ledger_path: Path | None = None,
        approval_ttl_seconds: float = 900,
        clock: Callable[[], datetime] = utc_now,
        approval_id_factory: Callable[[], str] | None = None,
    ) -> AsyncGenerator[LangGraphRuntime]:
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        ledger_path = execution_ledger_path or checkpoint_path.with_name(
            f"{checkpoint_path.stem}-tool-executions.sqlite"
        )
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        async with (
            AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver,
            aiosqlite.connect(ledger_path) as ledger_connection,
        ):
            await saver.setup()
            ledger = ToolExecutionLedger(ledger_connection)
            await ledger.setup()
            yield cls(
                model=model,
                saver=saver,
                ledger=ledger,
                tools=tools,
                tool_context=tool_context,
                approval_ttl_seconds=approval_ttl_seconds,
                clock=clock,
                approval_id_factory=approval_id_factory,
            )

    async def respond(self, context: RunContext, text: str) -> str:
        pending = await self._get_pending(context.conversation)
        if pending is not None:
            if not self._workflow.is_expired(pending):
                return self._workflow.approval_prompt(pending)
            expired = await self._resume(context, pending, approved=False, reason="expired")
            return self._response_text(expired)

        result = cast(
            AgentState,
            await self._graph.ainvoke(
                {
                    "messages": [HumanMessage(content=text)],
                    "actor_id": context.actor.user_id,
                    "conversation_id": context.conversation.conversation_id,
                },
                self._config(context.conversation),
                durability="sync",
            ),
        )
        return self._response_text(result)

    async def approve(self, context: RunContext, approval_id: str) -> str:
        return await self._decide(context, approval_id, approved=True)

    async def deny(self, context: RunContext, approval_id: str) -> str:
        return await self._decide(context, approval_id, approved=False)

    async def _decide(self, context: RunContext, approval_id: str, *, approved: bool) -> str:
        pending = await self._get_pending(context.conversation)
        if pending is None:
            return "No pending tool approval exists for this conversation."
        if approval_id != pending["approval_id"]:
            return "Approval ID does not match the pending request."
        if context.actor.user_id != pending["actor_id"]:
            return "Only the user who requested the tool can approve or deny it."
        if context.conversation.conversation_id != pending["conversation_id"]:
            return "Approval belongs to a different conversation."
        if self._workflow.is_expired(pending):
            result = await self._resume(context, pending, approved=False, reason="expired")
            return self._response_text(result)
        result = await self._resume(
            context,
            pending,
            approved=approved,
            reason="approved" if approved else "denied",
        )
        return self._response_text(result)

    async def _resume(
        self,
        context: RunContext,
        pending: PendingApproval,
        *,
        approved: bool,
        reason: str,
    ) -> AgentState:
        decision: ApprovalDecision = {
            "approval_id": pending["approval_id"],
            "approved": approved,
            "actor_id": context.actor.user_id,
            "conversation_id": context.conversation.conversation_id,
            "reason": reason,
        }
        return cast(
            AgentState,
            await self._graph.ainvoke(
                Command(resume=decision),
                self._config(context.conversation),
                durability="sync",
            ),
        )

    async def _get_pending(self, conversation: ConversationRef) -> PendingApproval | None:
        snapshot = await self._graph.aget_state(self._config(conversation))
        value = snapshot.values.get("pending_approval")
        return cast(PendingApproval, value) if isinstance(value, dict) else None

    def _response_text(self, result: AgentState) -> str:
        pending = result.get("pending_approval")
        if pending is not None:
            return self._workflow.approval_prompt(pending)
        last_message = result["messages"][-1]
        if not isinstance(last_message, AIMessage) or not isinstance(last_message.content, str):
            raise RuntimeError("LangGraph returned an invalid assistant response")
        return last_message.content

    @staticmethod
    def _config(conversation: ConversationRef) -> RunnableConfig:
        return {"configurable": {"thread_id": conversation.thread_id}}

    async def reset(self, conversation: ConversationRef) -> None:
        await self._saver.adelete_thread(conversation.thread_id)
        await self._ledger.clear_conversation(conversation.conversation_id)
