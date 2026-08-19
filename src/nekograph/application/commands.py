"""Deterministic commands that must never invoke an LLM."""

from dataclasses import dataclass

from nekograph.application.ports import AgentRuntime
from nekograph.models import RunContext


@dataclass(slots=True)
class CommandRouter:
    runtime: AgentRuntime

    async def dispatch(self, context: RunContext, text: str) -> str | None:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return None

        parts = stripped.split()
        command = parts[0].casefold()
        if command == "/help":
            return "Available commands: /help, /status, /reset, /approve <id>, /deny <id>"
        if command == "/status":
            return "NekoGraph is running. Agent runtime: LangGraph. Checkpoint: SQLite."
        if command == "/reset":
            await self.runtime.reset(context.conversation)
            return "Conversation context has been reset."
        if command == "/approve":
            if len(parts) != 2:
                return "Usage: /approve <approval_id>"
            return await self.runtime.approve(context, parts[1])
        if command == "/deny":
            if len(parts) != 2:
                return "Usage: /deny <approval_id>"
            return await self.runtime.deny(context, parts[1])
        return f"Unknown command: {command}. Use /help to list available commands."
