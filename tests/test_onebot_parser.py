from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from nekograph.models import (
    AnonymousSegment,
    ChatKind,
    DiceSegment,
    GroupBanNoticeEvent,
    HeartbeatEvent,
    ImageSegment,
    InboundMessageEvent,
    MentionSegment,
    MessageSentEvent,
    MFaceSegment,
    OneBotMetaEvent,
    OneBotNoticeEvent,
    OneBotRequestEvent,
    ReplySegment,
    RpsSegment,
    ShakeSegment,
    UnknownOneBotEvent,
    UnknownSegment,
)
from nekograph.protocols.onebot_v11.parser import (
    InvalidOneBotEventError,
    UnsupportedEventError,
    parse_message_event,
    parse_onebot_event,
)
from nekograph.protocols.onebot_v11.segments import parse_cq_message

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_private_message_preserves_protocol_extras_at_boundary() -> None:
    event = parse_message_event(load_fixture("private_message.json"))

    assert event.bot_id == "10000"
    assert event.message.chat.kind is ChatKind.PRIVATE
    assert event.message.chat.chat_id == "20001"
    assert event.message.actor.display_name == "Alice"
    assert event.message.plain_text == "hello neko"
    assert event.message.extra["napcat_extra_not_in_core_model"] == "ignored"


def test_parse_group_message_preserves_framework_segments() -> None:
    event = parse_message_event(load_fixture("group_message.json"))

    assert event.message.chat.kind is ChatKind.GROUP
    assert event.message.chat.chat_id == "30001"
    assert event.message.actor.user_id == "20002"
    assert event.message.actor.display_name == "Bob in group"
    assert [segment.kind for segment in event.message.segments] == ["at", "text"]
    assert event.message.plain_text == " explain LangGraph"


def test_non_message_event_is_explicitly_unsupported() -> None:
    with pytest.raises(UnsupportedEventError, match="post_type"):
        parse_message_event({"post_type": "notice"})


def test_malformed_group_message_is_diagnostic() -> None:
    payload = load_fixture("group_message.json")
    assert isinstance(payload, dict)
    event_payload = cast(dict[str, object], payload)
    event_payload.pop("group_id")

    with pytest.raises(InvalidOneBotEventError, match="group_id"):
        parse_message_event(event_payload)


def test_cq_string_is_converted_to_typed_segments() -> None:
    segments = parse_cq_message("hello[CQ:at,qq=10000][CQ:image,file=a.jpg]tail")

    assert segments[0].text_content == "hello"
    assert isinstance(segments[1], MentionSegment)
    assert segments[1].user_id == "10000"
    assert isinstance(segments[2], ImageSegment)
    assert segments[2].data["file"] == "a.jpg"
    assert segments[3].text_content == "tail"


def test_standard_and_napcat_segments_are_not_flattened() -> None:
    payload = cast(dict[str, object], load_fixture("private_message.json"))
    assert isinstance(payload, dict)
    payload["message"] = [
        {"type": "reply", "data": {"id": 1}},
        {"type": "face", "data": {"id": "14", "raw": "extra"}},
        {"type": "mface", "data": {"emoji_id": "e1"}},
        {"type": "new_napcat_type", "data": {"value": "kept"}},
    ]

    event = parse_message_event(payload)

    assert isinstance(event.message.segments[0], ReplySegment)
    assert event.message.segments[0].message_id == "1"
    assert event.message.segments[1].data["raw"] == "extra"
    assert isinstance(event.message.segments[-1], UnknownSegment)
    assert event.message.segments[-1].data == {"value": "kept"}


@pytest.mark.parametrize(
    ("segment_type", "expected_type"),
    [
        ("rps", RpsSegment),
        ("dice", DiceSegment),
        ("shake", ShakeSegment),
        ("anonymous", AnonymousSegment),
        ("mface", MFaceSegment),
    ],
)
def test_known_onebot_and_napcat_segments_have_explicit_types(
    segment_type: str, expected_type: type[object]
) -> None:
    segments = parse_cq_message(f"[CQ:{segment_type}]")
    assert isinstance(segments[0], expected_type)


@pytest.mark.parametrize(
    "segment_type",
    [
        "text",
        "face",
        "image",
        "record",
        "video",
        "file",
        "at",
        "rps",
        "dice",
        "shake",
        "anonymous",
        "mface",
        "share",
        "contact",
        "location",
        "music",
        "reply",
        "poke",
        "forward",
        "node",
        "nodes",
        "xml",
        "json",
        "markdown",
    ],
)
def test_documented_segment_matrix_never_falls_back_to_unknown(
    segment_type: str,
) -> None:
    segments = parse_cq_message(f"[CQ:{segment_type}]")
    assert not isinstance(segments[0], UnknownSegment)


def test_all_event_families_have_distinct_internal_models() -> None:
    base = {"time": 1723968000, "self_id": 10000}

    message = parse_onebot_event(
        {
            **base,
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "user_id": 2,
            "message": [{"type": "text", "data": {"text": "hi"}}],
        }
    )
    sent = parse_onebot_event(
        {
            **base,
            "post_type": "message_sent",
            "message_type": "private",
            "message_id": 2,
            "user_id": 2,
            "message": "hi",
        }
    )
    notice = parse_onebot_event(
        {
            **base,
            "post_type": "notice",
            "notice_type": "group_ban",
            "sub_type": "ban",
            "group_id": 3,
            "user_id": 2,
            "operator_id": 1,
            "duration": 60,
        }
    )
    request = parse_onebot_event(
        {
            **base,
            "post_type": "request",
            "request_type": "friend",
            "user_id": 2,
            "request_id": 4,
            "comment": "hello",
            "flag": "flag",
        }
    )
    meta = parse_onebot_event(
        {
            **base,
            "post_type": "meta_event",
            "meta_event_type": "heartbeat",
            "status": {"online": True, "good": True},
            "interval": 5000,
        }
    )

    assert isinstance(message, InboundMessageEvent)
    assert not isinstance(message, MessageSentEvent)
    assert isinstance(sent, MessageSentEvent)
    assert isinstance(notice, OneBotNoticeEvent)
    assert isinstance(notice, GroupBanNoticeEvent)
    assert notice.operator_id == "1"
    assert notice.duration_seconds == 60
    assert isinstance(request, OneBotRequestEvent)
    assert isinstance(meta, OneBotMetaEvent)
    assert isinstance(meta, HeartbeatEvent)
    assert meta.status["good"] is True
    assert meta.interval_ms == 5000


def test_unknown_event_is_preserved_without_claiming_known_semantics() -> None:
    event = parse_onebot_event(
        {
            "time": 1723968000,
            "self_id": 10000,
            "post_type": "notice",
            "notice_type": "future_napcat_notice",
            "value": {"new": True},
        }
    )

    assert isinstance(event, UnknownOneBotEvent)
    assert event.event_type == "future_napcat_notice"
    assert event.payload["value"] == {"new": True}


def test_missing_message_fields_are_rejected() -> None:
    with pytest.raises(InvalidOneBotEventError, match="message_id"):
        parse_onebot_event(
            {
                "time": 1723968000,
                "self_id": 10000,
                "post_type": "message",
                "message_type": "private",
                "user_id": 2,
                "message": "hello",
            }
        )
