"""Local terminal adapter backed by the framework's internal message model."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import count

from nekograph.application import MessageApplication
from nekograph.models import (
    Actor,
    Chat,
    ChatKind,
    InboundMessageEvent,
    Message,
    MessageSegment,
    OutboundMessage,
)

LineReader = Callable[[str], Awaitable[str]]
LineWriter = Callable[[str], None]


async def read_console_line(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


def write_console_line(text: str) -> None:
    print(text)


@dataclass(slots=True)
class LocalConsoleAdapter:
    application: MessageApplication
    reader: LineReader = read_console_line
    writer: LineWriter = write_console_line
    bot_id: str = "local-agent"
    user_id: str = "local-user"
    _message_ids: count[int] = field(default_factory=lambda: count(1), init=False, repr=False)

    async def run(self) -> None:
        self.writer("NekoGraph local chat started. Type /exit or /quit to stop.")
        while True:
            try:
                text = await self.reader("You> ")
            except EOFError:
                break

            stripped = text.strip()
            if not stripped:
                continue
            if stripped.casefold() in {"/exit", "/quit"}:
                break

            response = await self.application.handle(self.build_event(text))
            if response is not None:
                self.writer(f"NekoGraph> {self._text(response)}")

    def build_event(self, text: str) -> InboundMessageEvent:
        chat = Chat(kind=ChatKind.PRIVATE, chat_id=self.user_id)
        return InboundMessageEvent(
            bot_id=self.bot_id,
            message=Message(
                message_id=f"local-{next(self._message_ids)}",
                timestamp=datetime.now(UTC),
                actor=Actor(user_id=self.user_id, display_name="Local User"),
                chat=chat,
                segments=(MessageSegment.text(text),),
                plain_text=text,
            ),
        )

    @staticmethod
    def _text(message: OutboundMessage) -> str:
        return "".join(segment.text_content for segment in message.segments)
