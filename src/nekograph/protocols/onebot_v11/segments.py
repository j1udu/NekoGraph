"""OneBot array/CQ message conversion into framework-owned segments."""

# pyright: reportUnknownArgumentType=false, reportUnknownVariableType=false

from __future__ import annotations

import html
import re
from typing import Any, cast

from nekograph.models import (
    AnonymousSegment,
    ContactSegment,
    DiceSegment,
    FaceSegment,
    FileSegment,
    ForwardNodeSegment,
    ForwardNodesSegment,
    ForwardSegment,
    ImageSegment,
    JsonSegment,
    LocationSegment,
    MarkdownSegment,
    MentionSegment,
    MessageSegment,
    MFaceSegment,
    MusicSegment,
    PokeSegment,
    RecordSegment,
    ReplySegment,
    RpsSegment,
    ShakeSegment,
    ShareSegment,
    TextSegment,
    UnknownSegment,
    VideoSegment,
    XmlSegment,
)


class InvalidOneBotMessageError(ValueError):
    """A OneBot message or segment has an invalid structure."""


_SEGMENT_TYPES: dict[str, type[MessageSegment]] = {
    "text": TextSegment,
    "plain": TextSegment,
    "face": FaceSegment,
    "image": ImageSegment,
    "record": RecordSegment,
    "video": VideoSegment,
    "file": FileSegment,
    "at": MentionSegment,
    "rps": RpsSegment,
    "dice": DiceSegment,
    "shake": ShakeSegment,
    "anonymous": AnonymousSegment,
    "mface": MFaceSegment,
    "share": ShareSegment,
    "contact": ContactSegment,
    "location": LocationSegment,
    "music": MusicSegment,
    "reply": ReplySegment,
    "poke": PokeSegment,
    "forward": ForwardSegment,
    "node": ForwardNodeSegment,
    "nodes": ForwardNodesSegment,
    "xml": XmlSegment,
    "json": JsonSegment,
    "markdown": MarkdownSegment,
}
_CQ_RE = re.compile(r"\[CQ:(?P<type>[A-Za-z0-9_-]+)(?P<params>,[^\]]*)?\]")


def _object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidOneBotMessageError(f"{field} must be an object")
    return cast(dict[str, Any], value)


def _required(payload: dict[str, Any], field: str) -> Any:
    if field not in payload or payload[field] is None:
        raise InvalidOneBotMessageError(f"message is missing required field: {field}")
    return payload[field]


def _string(value: object, field: str) -> str:
    if isinstance(value, (str, int)):
        return str(value)
    raise InvalidOneBotMessageError(f"{field} must be a string or integer")


def _parse_cq_value(value: str) -> str:
    return html.unescape(
        value.replace("&#44;", ",")
        .replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&#38;", "&")
    )


def _segment(segment_type: str, data: dict[str, Any]) -> MessageSegment:
    normalized = "text" if segment_type == "plain" else segment_type
    if normalized == "text":
        return TextSegment(kind="text", data={"text": str(data.get("text", ""))})
    model = _SEGMENT_TYPES.get(normalized)
    if model is None:
        return UnknownSegment(kind=segment_type, data=data, original_type=segment_type)
    return model(kind=normalized, data=data)


def parse_cq_message(value: str) -> tuple[MessageSegment, ...]:
    """Parse OneBot's legacy CQ string format into internal segments."""
    segments: list[MessageSegment] = []
    cursor = 0
    for match in _CQ_RE.finditer(value):
        if match.start() > cursor:
            segments.append(MessageSegment.text(value[cursor : match.start()]))
        data: dict[str, Any] = {}
        raw_params = match.group("params") or ""
        for item in raw_params[1:].split(",") if raw_params else ():
            if "=" not in item:
                continue
            key, raw = item.split("=", 1)
            data[key] = _parse_cq_value(raw)
        segments.append(_segment(match.group("type"), data))
        cursor = match.end()
    if cursor < len(value):
        segments.append(MessageSegment.text(value[cursor:]))
    return tuple(segments) if segments else (MessageSegment.text(value),)


def parse_message_segments(value: object) -> tuple[MessageSegment, ...]:
    """Parse OneBot array or CQ-string messages into one segment sequence."""
    if isinstance(value, str):
        return parse_cq_message(value)
    if not isinstance(value, list):
        raise InvalidOneBotMessageError("message must be a string or array")
    result: list[MessageSegment] = []
    for index, item in enumerate(value):
        segment = _object(item, f"message[{index}]")
        segment_type = _string(_required(segment, "type"), f"message[{index}].type")
        raw_data = segment.get("data", {})
        data = {} if raw_data is None else _object(raw_data, f"message[{index}].data")
        result.append(_segment(segment_type, data))
    return tuple(result)
