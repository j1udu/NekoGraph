"""End-to-end application routing after protocol normalization."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from nekograph.application.commands import CommandRouter
from nekograph.application.conversation import ConversationResolver
from nekograph.application.ports import AgentRuntime
from nekograph.application.scheduler import ConversationScheduler
from nekograph.application.wakeup import WakeupPolicy
from nekograph.logging import fields
from nekograph.models import InboundMessageEvent, OutboundMessage, RunContext

logger = logging.getLogger(__name__)


class MessageApplication:
    def __init__(
        self,
        *,
        runtime: AgentRuntime,
        conversations: ConversationResolver,
        wakeup: WakeupPolicy,
        scheduler: ConversationScheduler | None = None,
    ) -> None:
        self._runtime = runtime
        self._conversations = conversations
        self._wakeup = wakeup
        self._scheduler = scheduler or ConversationScheduler()
        self._commands = CommandRouter(runtime)

    async def handle(self, event: InboundMessageEvent) -> OutboundMessage | None:
        decision = self._wakeup.evaluate(event)
        if not decision.awake:
            logger.debug(
                "message_ignored",
                extra=fields(bot_id=event.bot_id, message_id=event.message.message_id),
            )
            return None

        conversation = self._conversations.resolve(event)
        context = RunContext(
            run_id=uuid4().hex,
            bot_id=event.bot_id,
            actor=event.message.actor,
            chat=event.message.chat,
            conversation=conversation,
        )

        async def process() -> OutboundMessage:
            command_response = await self._commands.dispatch(context, decision.text)
            content = (
                command_response
                if command_response is not None
                else await self._runtime.respond(context, decision.text)
            )
            return OutboundMessage.text(
                bot_id=event.bot_id,
                chat=event.message.chat,
                content=content,
                reply_to=event.message.message_id,
            )

        try:
            return await self._scheduler.run(conversation.conversation_id, process)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "message_processing_failed",
                extra=fields(
                    run_id=context.run_id,
                    conversation_id=conversation.conversation_id,
                    message_id=event.message.message_id,
                ),
            )
            return OutboundMessage.text(
                bot_id=event.bot_id,
                chat=event.message.chat,
                content="NekoGraph failed to process this message. Please try again later.",
                reply_to=event.message.message_id,
            )
