"""Framework-owned models shared across protocol and runtime boundaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChatKind(StrEnum):
    PRIVATE = "private"
    GROUP = "group"


class GroupConversationMode(StrEnum):
    SHARED = "shared"
    PER_USER = "per_user"


class MessageSegment(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def text(cls, content: str) -> MessageSegment:
        return cls(kind="text", data={"text": content})

    @property
    def text_content(self) -> str:
        value = self.data.get("text")
        return value if self.kind == "text" and isinstance(value, str) else ""


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    display_name: str | None = None


class Chat(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: ChatKind
    chat_id: str


class Message(BaseModel):
    model_config = ConfigDict(frozen=True)

    message_id: str
    timestamp: datetime
    actor: Actor
    chat: Chat
    segments: tuple[MessageSegment, ...]
    plain_text: str


class InboundMessageEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    message: Message


class ConversationRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    conversation_id: str
    thread_id: str


class RunContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    bot_id: str
    actor: Actor
    chat: Chat
    conversation: ConversationRef


class OutboundMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    chat: Chat
    segments: tuple[MessageSegment, ...]
    reply_to: str | None = None

    @classmethod
    def text(
        cls,
        *,
        bot_id: str,
        chat: Chat,
        content: str,
        reply_to: str | None = None,
    ) -> OutboundMessage:
        return cls(
            bot_id=bot_id,
            chat=chat,
            segments=(MessageSegment.text(content),),
            reply_to=reply_to,
        )
