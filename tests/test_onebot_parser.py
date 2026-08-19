from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from nekograph.models import ChatKind
from nekograph.protocols.onebot_v11.parser import (
    InvalidOneBotEventError,
    UnsupportedEventError,
    parse_message_event,
)

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_parse_private_message_drops_protocol_extras() -> None:
    event = parse_message_event(load_fixture("private_message.json"))

    assert event.bot_id == "10000"
    assert event.message.chat.kind is ChatKind.PRIVATE
    assert event.message.chat.chat_id == "20001"
    assert event.message.actor.display_name == "Alice"
    assert event.message.plain_text == "hello neko"
    assert "napcat" not in event.model_dump_json()
    assert "raw_message" not in event.model_dump_json()


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
