"""Pure OneBot v11 message parsing without application side effects."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nekograph.models import (
    Actor,
    Chat,
    ChatKind,
    InboundMessageEvent,
    Message,
    MessageSegment,
)


class UnsupportedEventError(ValueError):
    """The payload is valid JSON but isn't a supported message event."""


class InvalidOneBotEventError(ValueError):
    """The payload claims to be a message event but is malformed."""


class _OneBotSegment(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str
    data: dict[str, Any] = Field(default_factory=dict)


class _OneBotSender(BaseModel):
    model_config = ConfigDict(extra="allow")

    nickname: str | None = None
    card: str | None = None


class _OneBotMessageEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    time: int
    self_id: int | str
    post_type: Literal["message"]
    message_type: Literal["private", "group"]
    message_id: int | str
    user_id: int | str
    group_id: int | str | None = None
    message: str | list[_OneBotSegment]
    raw_message: str = ""
    sender: _OneBotSender | None = None


def _segments(value: str | list[_OneBotSegment]) -> tuple[MessageSegment, ...]:
    if isinstance(value, str):
        return (MessageSegment.text(value),)
    return tuple(MessageSegment(kind=item.type, data=item.data) for item in value)


def parse_message_event(payload: object) -> InboundMessageEvent:
    if not isinstance(payload, dict):
        raise InvalidOneBotEventError("OneBot event must be a JSON object")
    event_payload = cast(dict[str, object], payload)
    if event_payload.get("post_type") != "message":
        raise UnsupportedEventError(f"unsupported post_type: {event_payload.get('post_type')!r}")

    try:
        raw = _OneBotMessageEvent.model_validate(event_payload)
    except ValidationError as exc:
        raise InvalidOneBotEventError(str(exc)) from exc

    if raw.message_type == "group" and raw.group_id is None:
        raise InvalidOneBotEventError("group message is missing group_id")

    segments = _segments(raw.message)
    display_name = None
    if raw.sender is not None:
        display_name = raw.sender.card or raw.sender.nickname
    chat = Chat(
        kind=ChatKind(raw.message_type),
        chat_id=str(raw.user_id if raw.message_type == "private" else raw.group_id),
    )
    message = Message(
        message_id=str(raw.message_id),
        timestamp=datetime.fromtimestamp(raw.time, tz=UTC),
        actor=Actor(user_id=str(raw.user_id), display_name=display_name),
        chat=chat,
        segments=segments,
        plain_text="".join(segment.text_content for segment in segments),
    )
    return InboundMessageEvent(bot_id=str(raw.self_id), message=message)
