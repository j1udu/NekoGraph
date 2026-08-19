"""Narrow capabilities required by the application layer."""

from typing import Protocol

from nekograph.models import ConversationRef, RunContext


class AgentRuntime(Protocol):
    async def respond(self, context: RunContext, text: str) -> str: ...

    async def approve(self, context: RunContext, approval_id: str) -> str: ...

    async def deny(self, context: RunContext, approval_id: str) -> str: ...

    async def reset(self, conversation: ConversationRef) -> None: ...
