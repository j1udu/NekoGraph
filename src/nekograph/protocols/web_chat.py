"""Browser chat adapter that emits only framework-owned message models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from nekograph.application.conversation import ConversationResolver
from nekograph.application.service import MessageApplication
from nekograph.models import (
    Actor,
    Chat,
    ChatKind,
    ConversationRef,
    InboundMessageEvent,
    Message,
    MessageSegment,
    OutboundMessage,
)


@dataclass(frozen=True, slots=True)
class WebChatAdapter:
    application: MessageApplication
    bot_id: str = "web-agent"

    async def send(self, conversation_id: str, text: str) -> OutboundMessage | None:
        return await self.application.handle(self.build_event(conversation_id, text))

    def conversation(self, conversation_id: str) -> ConversationRef:
        return ConversationResolver(namespace="web:v1").resolve(
            self.build_event(conversation_id, "")
        )

    def build_event(self, conversation_id: str, text: str) -> InboundMessageEvent:
        actor_id = f"web-user:{conversation_id}"
        chat = Chat(kind=ChatKind.PRIVATE, chat_id=conversation_id)
        return InboundMessageEvent(
            bot_id=self.bot_id,
            message=Message(
                message_id=f"web-{uuid4().hex}",
                timestamp=datetime.now(UTC),
                actor=Actor(user_id=actor_id, display_name="Web User"),
                chat=chat,
                segments=(MessageSegment.text(text),),
                plain_text=text,
            ),
        )
