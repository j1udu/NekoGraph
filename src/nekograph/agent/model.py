"""Model capability used by the graph, with a deterministic test implementation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage

from nekograph.model_types import ModelToolSpec


class ChatModel(Protocol):
    async def complete(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[ModelToolSpec] = (),
    ) -> AIMessage: ...


def _message_text(message: AnyMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


@dataclass(slots=True)
class FakeChatModel:
    """A deterministic model that exposes the persisted turn count in its response."""

    scripted_responses: list[AIMessage] = field(default_factory=lambda: [])
    calls: int = 0
    received_snapshots: list[tuple[tuple[str, str], ...]] = field(default_factory=lambda: [])
    received_tools: list[tuple[ModelToolSpec, ...]] = field(default_factory=lambda: [])

    async def complete(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[ModelToolSpec] = (),
    ) -> AIMessage:
        self.calls += 1
        snapshot = tuple((message.type, _message_text(message)) for message in messages)
        self.received_snapshots.append(snapshot)
        self.received_tools.append(tuple(tools))
        if self.scripted_responses:
            return self.scripted_responses.pop(0)
        user_messages = [message for message in messages if isinstance(message, HumanMessage)]
        latest = _message_text(user_messages[-1]) if user_messages else ""
        return AIMessage(content=f"Fake response turn {len(user_messages)}: {latest}")
