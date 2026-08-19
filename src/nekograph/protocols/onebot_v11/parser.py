# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
# pyright: reportUnknownVariableType=false
# pyright: reportArgumentType=false

"""Pure OneBot v11/NapCat event and message-segment parsing.

The parser is deliberately side-effect free.  It converts protocol payloads to
NekoGraph models and keeps unknown fields in ``extra``/``payload`` so a newer
NapCat event cannot silently destroy the original information.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pydantic import ValidationError

from nekograph.models import (
    Actor,
    BotOfflineNoticeEvent,
    Chat,
    ChatKind,
    FriendAddNoticeEvent,
    FriendRecallNoticeEvent,
    FriendRequestEvent,
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    GroupCardNoticeEvent,
    GroupEmojiLikeNoticeEvent,
    GroupEssenceNoticeEvent,
    GroupGrayTipNoticeEvent,
    GroupHonorNoticeEvent,
    GroupLuckyKingNoticeEvent,
    GroupMemberDecreaseNoticeEvent,
    GroupMemberIncreaseNoticeEvent,
    GroupNameNoticeEvent,
    GroupRecallNoticeEvent,
    GroupRequestEvent,
    GroupTitleNoticeEvent,
    GroupUploadNoticeEvent,
    HeartbeatEvent,
    InboundMessageEvent,
    InputStatusNoticeEvent,
    LifecycleEvent,
    Message,
    MessageSentEvent,
    OneBotEvent,
    OneBotNoticeEvent,
    PokeNoticeEvent,
    ProfileLikeNoticeEvent,
    UnknownOneBotEvent,
)
from nekograph.protocols.onebot_v11.segments import (
    InvalidOneBotMessageError,
    parse_message_segments,
)


class UnsupportedEventError(ValueError):
    """The payload is valid JSON but is not in the supported event families."""


class InvalidOneBotEventError(ValueError):
    """The payload claims to be a OneBot event but is malformed."""


_NOTICE_TYPES = {
    "group_upload",
    "group_admin",
    "group_decrease",
    "group_increase",
    "group_ban",
    "friend_add",
    "group_recall",
    "friend_recall",
    "group_poke",
    "friend_poke",
    "group_lucky_king",
    "group_honor",
    "group_card",
    "group_title",
    "essence",
    "group_msg_emoji_like",
    "notify",
    "bot_offline",
}
_REQUEST_TYPES = {"friend", "group"}
_META_TYPES = {"lifecycle", "heartbeat"}


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidOneBotEventError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _required(payload: dict[str, Any], field: str) -> Any:
    if field not in payload or payload[field] is None:
        raise InvalidOneBotEventError(f"event is missing required field: {field}")
    return payload[field]


def _string(value: object, field: str) -> str:
    if isinstance(value, (str, int)):
        return str(value)
    raise InvalidOneBotEventError(f"{field} must be a string or integer")


def _integer(value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise InvalidOneBotEventError(f"{field} must be an integer")


def _optional_id(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    return _string(value, field) if value is not None else None


def _need(value: str | None, field: str) -> str:
    if value is None:
        raise InvalidOneBotEventError(f"event is missing required field: {field}")
    return value


def _timestamp(payload: dict[str, Any]) -> datetime:
    value = _required(payload, "time")
    if not isinstance(value, (int, float)):
        raise InvalidOneBotEventError("time must be a number")
    return datetime.fromtimestamp(value, tz=UTC)


def _actor(payload: dict[str, Any]) -> Actor:
    sender_value = payload.get("sender")
    sender = sender_value if isinstance(sender_value, dict) else {}
    user_id = _string(payload.get("user_id", sender.get("user_id")), "user_id")
    nickname = sender.get("nickname")
    card = sender.get("card")
    display_name = card or nickname
    return Actor(
        user_id=user_id,
        display_name=str(display_name) if display_name is not None else None,
        nickname=str(nickname) if nickname is not None else None,
        card=str(card) if card is not None else None,
        sex=str(sender["sex"]) if sender.get("sex") is not None else None,
        age=int(sender["age"]) if isinstance(sender.get("age"), int) else None,
        area=str(sender["area"]) if sender.get("area") is not None else None,
        level=str(sender["level"]) if sender.get("level") is not None else None,
        role=str(sender["role"]) if sender.get("role") is not None else None,
        title=str(sender["title"]) if sender.get("title") is not None else None,
    )


def _parse_message(
    payload: dict[str, Any], *, sent: bool
) -> InboundMessageEvent | MessageSentEvent:
    message_type = _string(_required(payload, "message_type"), "message_type")
    if message_type not in {"private", "group"}:
        raise InvalidOneBotEventError(f"unsupported message_type: {message_type}")
    group_id = payload.get("group_id")
    if message_type == "group" and group_id is None:
        raise InvalidOneBotEventError("group message is missing group_id")
    actor = _actor(payload)
    try:
        segments = parse_message_segments(_required(payload, "message"))
    except InvalidOneBotMessageError as exc:
        raise InvalidOneBotEventError(str(exc)) from exc
    chat = Chat(
        kind=ChatKind(message_type),
        chat_id=_string(
            payload.get("user_id") if message_type == "private" else group_id,
            "chat_id",
        ),
    )
    known = {
        "time", "self_id", "post_type", "message_type", "sub_type", "message_id",
        "user_id", "group_id", "message", "raw_message", "sender", "anonymous", "font",
        "group_name",
    }
    message = Message(
        message_id=_string(_required(payload, "message_id"), "message_id"),
        timestamp=_timestamp(payload),
        actor=actor,
        chat=chat,
        segments=segments,
        plain_text="".join(segment.text_content for segment in segments),
        sub_type=str(payload["sub_type"]) if payload.get("sub_type") is not None else None,
        raw_message=str(payload["raw_message"]) if payload.get("raw_message") is not None else None,
        anonymous=payload.get("anonymous") if isinstance(payload.get("anonymous"), dict) else None,
        font=int(payload["font"]) if isinstance(payload.get("font"), int) else None,
        group_name=(
            str(payload["group_name"])
            if payload.get("group_name") is not None
            else None
        ),
        extra={key: value for key, value in payload.items() if key not in known},
    )
    event_type = MessageSentEvent if sent else InboundMessageEvent
    return event_type(bot_id=_string(_required(payload, "self_id"), "self_id"), message=message)


def _parse_notify_notice(
    common: dict[str, Any],
    payload: dict[str, Any],
    sub_type: object,
    group_id: str | None,
    user_id: str | None,
    operator_id: str | None,
    message_id: str | None,
) -> OneBotNoticeEvent:
    if sub_type == "poke":
        return PokeNoticeEvent(
            **common,
            user_id=_need(user_id, "user_id"),
            target_id=_string(_required(payload, "target_id"), "target_id"),
            group_id=group_id,
        )
    if sub_type == "lucky_king":
        return GroupLuckyKingNoticeEvent(
            **common,
            group_id=_need(group_id, "group_id"),
            user_id=user_id,
            target_id=_string(_required(payload, "target_id"), "target_id"),
        )
    if sub_type == "honor":
        return GroupHonorNoticeEvent(
            **common,
            group_id=_need(group_id, "group_id"),
            user_id=user_id,
            honor_type=(
                str(payload["honor_type"])
                if payload.get("honor_type") is not None
                else None
            ),
        )
    if sub_type == "title":
        return GroupTitleNoticeEvent(
            **common,
            group_id=_need(group_id, "group_id"),
            user_id=user_id,
            title=str(payload.get("title", "")),
        )
    if sub_type == "group_name":
        return GroupNameNoticeEvent(
            **common,
            group_id=_need(group_id, "group_id"),
            user_id=user_id,
            name_new=str(payload.get("name_new", "")),
        )
    if sub_type == "gray_tip":
        return GroupGrayTipNoticeEvent(
            **common,
            group_id=_need(group_id, "group_id"),
            user_id=user_id,
            message_id=_need(message_id, "message_id"),
            business_id=(
                str(payload["busi_id"])
                if payload.get("busi_id") is not None
                else None
            ),
            content=str(payload.get("content", "")),
        )
    if sub_type == "profile_like":
        return ProfileLikeNoticeEvent(
            **common,
            operator_id=_need(operator_id, "operator_id"),
            operator_nick=(
                str(payload["operator_nick"])
                if payload.get("operator_nick") is not None
                else None
            ),
            times=_integer(_required(payload, "times"), "times"),
        )
    if sub_type == "input_status":
        return InputStatusNoticeEvent(
            **common,
            user_id=_need(user_id, "user_id"),
            group_id=group_id,
            status_text=str(payload.get("status_text", "")),
            event_type=(
                _integer(payload["event_type"], "event_type")
                if payload.get("event_type") is not None
                else None
            ),
        )
    return OneBotNoticeEvent(**common)


def _generic_event(payload: dict[str, Any], post_type: str) -> OneBotEvent:
    timestamp = _timestamp(payload) if "time" in payload else None
    if post_type == "notice":
        notice_type = _string(_required(payload, "notice_type"), "notice_type")
        if notice_type not in _NOTICE_TYPES:
            return UnknownOneBotEvent(
                bot_id=str(payload["self_id"]),
                timestamp=timestamp,
                post_type=post_type,
                event_type=notice_type,
                payload=dict(payload),
            )
        common = {
            "bot_id": _string(_required(payload, "self_id"), "self_id"),
            "timestamp": cast(datetime, timestamp),
            "notice_type": notice_type,
            "sub_type": (
                str(payload["sub_type"])
                if payload.get("sub_type") is not None
                else None
            ),
            "payload": dict(payload),
        }
        group_id = _optional_id(payload, "group_id")
        user_id = _optional_id(payload, "user_id")
        operator_id = _optional_id(payload, "operator_id")
        message_id = _optional_id(payload, "message_id")
        sub_type = common["sub_type"]
        if notice_type == "group_upload":
            return GroupUploadNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                file=_object(_required(payload, "file"), "file"),
            )
        if notice_type == "group_admin":
            return GroupAdminNoticeEvent(
                **common, group_id=_need(group_id, "group_id"), user_id=user_id
            )
        if notice_type == "group_decrease":
            return GroupMemberDecreaseNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                operator_id=_need(operator_id, "operator_id"),
            )
        if notice_type == "group_increase":
            return GroupMemberIncreaseNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                operator_id=_need(operator_id, "operator_id"),
            )
        if notice_type == "group_ban":
            return GroupBanNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                operator_id=_need(operator_id, "operator_id"),
                duration_seconds=_integer(_required(payload, "duration"), "duration"),
            )
        if notice_type == "friend_add":
            return FriendAddNoticeEvent(**common, user_id=_need(user_id, "user_id"))
        if notice_type == "group_recall":
            return GroupRecallNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                operator_id=_need(operator_id, "operator_id"),
                message_id=_need(message_id, "message_id"),
            )
        if notice_type == "friend_recall":
            return FriendRecallNoticeEvent(
                **common,
                user_id=_need(user_id, "user_id"),
                message_id=_need(message_id, "message_id"),
            )
        if notice_type == "group_card":
            return GroupCardNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                card_old=str(payload.get("card_old", "")),
                card_new=str(payload.get("card_new", "")),
            )
        if notice_type == "essence":
            return GroupEssenceNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                message_id=_need(message_id, "message_id"),
                sender_id=_string(_required(payload, "sender_id"), "sender_id"),
                operator_id=_need(operator_id, "operator_id"),
            )
        if notice_type == "group_msg_emoji_like":
            likes_value = payload.get("likes", [])
            likes = tuple(
                _object(item, "likes[]")
                for item in likes_value
            ) if isinstance(likes_value, list) else ()
            return GroupEmojiLikeNoticeEvent(
                **common,
                group_id=_need(group_id, "group_id"),
                user_id=user_id,
                message_id=_need(message_id, "message_id"),
                likes=likes,
            )
        if notice_type == "bot_offline":
            return BotOfflineNoticeEvent(
                **common,
                user_id=_need(user_id, "user_id"),
                tag=str(payload["tag"]) if payload.get("tag") is not None else None,
                message=(
                    str(payload["message"])
                    if payload.get("message") is not None
                    else None
                ),
            )
        if notice_type == "notify":
            return _parse_notify_notice(
                common, payload, sub_type, group_id, user_id, operator_id, message_id
            )
        return OneBotNoticeEvent(**common)
    if post_type == "request":
        request_type = _string(_required(payload, "request_type"), "request_type")
        if request_type not in _REQUEST_TYPES:
            return UnknownOneBotEvent(
                bot_id=str(payload["self_id"]),
                timestamp=timestamp,
                post_type=post_type,
                event_type=request_type,
                payload=dict(payload),
            )
        common_request = {
            "bot_id": _string(_required(payload, "self_id"), "self_id"),
            "timestamp": cast(datetime, timestamp),
            "request_type": request_type,
            "sub_type": (
                str(payload["sub_type"])
                if payload.get("sub_type") is not None
                else None
            ),
            "request_id": (
                str(payload["request_id"])
                if payload.get("request_id") is not None
                else None
            ),
            "comment": (
                str(payload["comment"])
                if payload.get("comment") is not None
                else None
            ),
            "payload": dict(payload),
        }
        if request_type == "friend":
            return FriendRequestEvent(
                **common_request,
                user_id=_string(_required(payload, "user_id"), "user_id"),
                flag=_string(_required(payload, "flag"), "flag"),
            )
        return GroupRequestEvent(
            **common_request,
            user_id=_string(_required(payload, "user_id"), "user_id"),
            group_id=_string(_required(payload, "group_id"), "group_id"),
            flag=_string(_required(payload, "flag"), "flag"),
        )
    meta_event_type = _string(_required(payload, "meta_event_type"), "meta_event_type")
    if meta_event_type not in _META_TYPES:
        return UnknownOneBotEvent(
            bot_id=str(payload["self_id"]),
            timestamp=timestamp,
            post_type=post_type,
            event_type=meta_event_type,
            payload=dict(payload),
        )
    common_meta = {
        "bot_id": _string(_required(payload, "self_id"), "self_id"),
        "timestamp": cast(datetime, timestamp),
        "meta_event_type": meta_event_type,
        "sub_type": (
            str(payload["sub_type"])
            if payload.get("sub_type") is not None
            else None
        ),
        "payload": dict(payload),
    }
    if meta_event_type == "heartbeat":
        return HeartbeatEvent(
            **common_meta,
            status=_object(_required(payload, "status"), "status"),
            interval_ms=_integer(_required(payload, "interval"), "interval"),
        )
    return LifecycleEvent(
        **common_meta,
    )


def parse_onebot_event(payload: object) -> OneBotEvent:
    if not isinstance(payload, dict):
        raise InvalidOneBotEventError("OneBot event must be a JSON object")
    event_payload: dict[str, Any] = cast(dict[str, Any], payload)
    post_type = event_payload.get("post_type")
    if not isinstance(post_type, str):
        raise InvalidOneBotEventError("event is missing post_type")
    try:
        if post_type == "message":
            return _parse_message(event_payload, sent=False)
        if post_type == "message_sent":
            return _parse_message(event_payload, sent=True)
        if post_type in {"notice", "request", "meta_event"}:
            return _generic_event(event_payload, post_type)
    except (ValidationError, TypeError, ValueError) as exc:
        if isinstance(exc, InvalidOneBotEventError):
            raise
        raise InvalidOneBotEventError(str(exc)) from exc
    return UnknownOneBotEvent(
        bot_id=str(event_payload["self_id"]) if event_payload.get("self_id") is not None else None,
        timestamp=_timestamp(event_payload) if "time" in event_payload else None,
        post_type=post_type,
        event_type=str(
            event_payload.get("notice_type")
            or event_payload.get("request_type")
            or event_payload.get("meta_event_type")
            or post_type
        ),
        payload=dict(event_payload),
    )


def parse_message_event(payload: object) -> InboundMessageEvent:
    """Backward-compatible parser for normal inbound message events."""
    if isinstance(payload, dict) and payload.get("post_type") != "message":
        raise UnsupportedEventError(
            f"unsupported post_type: {payload.get('post_type')!r}"
        )
    event = parse_onebot_event(payload)
    if not isinstance(event, InboundMessageEvent) or isinstance(event, MessageSentEvent):
        raise UnsupportedEventError("payload is not an inbound message event")
    return event
