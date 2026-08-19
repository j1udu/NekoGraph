"""Stable conversation identity rules."""

from dataclasses import dataclass

from nekograph.models import (
    ChatKind,
    ConversationRef,
    GroupConversationMode,
    InboundMessageEvent,
)


@dataclass(frozen=True, slots=True)
class ConversationResolver:
    group_mode: GroupConversationMode = GroupConversationMode.PER_USER

    def resolve(self, event: InboundMessageEvent) -> ConversationRef:
        message = event.message
        if message.chat.kind is ChatKind.PRIVATE:
            identity = f"qq:v1:bot:{event.bot_id}:private:{message.actor.user_id}"
        elif self.group_mode is GroupConversationMode.SHARED:
            identity = f"qq:v1:bot:{event.bot_id}:group:{message.chat.chat_id}"
        else:
            identity = (
                f"qq:v1:bot:{event.bot_id}:group:{message.chat.chat_id}"
                f":user:{message.actor.user_id}"
            )
        return ConversationRef(conversation_id=identity, thread_id=identity)
