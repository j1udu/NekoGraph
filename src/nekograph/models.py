"""Framework-owned models shared across protocol and runtime boundaries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, ClassVar

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


class KnownMessageSegment(MessageSegment):
    """A typed OneBot segment that still preserves protocol fields in ``data``."""

    protocol_type: ClassVar[str]


class TextSegment(KnownMessageSegment):
    protocol_type = "text"


class FaceSegment(KnownMessageSegment):
    protocol_type = "face"


class MFaceSegment(KnownMessageSegment):
    protocol_type = "mface"


class RpsSegment(KnownMessageSegment):
    protocol_type = "rps"


class DiceSegment(KnownMessageSegment):
    protocol_type = "dice"


class ShakeSegment(KnownMessageSegment):
    protocol_type = "shake"


class AnonymousSegment(KnownMessageSegment):
    protocol_type = "anonymous"


class ImageSegment(KnownMessageSegment):
    protocol_type = "image"


class RecordSegment(KnownMessageSegment):
    protocol_type = "record"


class VideoSegment(KnownMessageSegment):
    protocol_type = "video"


class FileSegment(KnownMessageSegment):
    protocol_type = "file"


class MentionSegment(KnownMessageSegment):
    protocol_type = "at"

    @property
    def user_id(self) -> str | None:
        value = self.data.get("qq")
        return str(value) if value is not None else None


class ReplySegment(KnownMessageSegment):
    protocol_type = "reply"

    @property
    def message_id(self) -> str | None:
        value = self.data.get("id")
        return str(value) if value is not None else None


class ShareSegment(KnownMessageSegment):
    protocol_type = "share"


class ContactSegment(KnownMessageSegment):
    protocol_type = "contact"


class LocationSegment(KnownMessageSegment):
    protocol_type = "location"


class MusicSegment(KnownMessageSegment):
    protocol_type = "music"


class PokeSegment(KnownMessageSegment):
    protocol_type = "poke"


class ForwardSegment(KnownMessageSegment):
    protocol_type = "forward"


class ForwardNodeSegment(KnownMessageSegment):
    protocol_type = "node"


class ForwardNodesSegment(KnownMessageSegment):
    protocol_type = "nodes"


class XmlSegment(KnownMessageSegment):
    protocol_type = "xml"


class JsonSegment(KnownMessageSegment):
    protocol_type = "json"


class MarkdownSegment(KnownMessageSegment):
    protocol_type = "markdown"


class UnknownSegment(MessageSegment):
    """A forward-compatible segment for OneBot/NapCat extensions."""

    original_type: str


class Actor(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    display_name: str | None = None
    nickname: str | None = None
    card: str | None = None
    sex: str | None = None
    age: int | None = None
    area: str | None = None
    level: str | None = None
    role: str | None = None
    title: str | None = None


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
    sub_type: str | None = None
    raw_message: str | None = None
    anonymous: dict[str, Any] | None = None
    font: int | None = None
    group_name: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class InboundMessageEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    message: Message
    post_type: str = "message"


class MessageSentEvent(InboundMessageEvent):
    """OneBot ``message_sent`` event, normalized like an inbound message."""

    post_type: str = "message_sent"


class OneBotNoticeEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    timestamp: datetime
    notice_type: str
    sub_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class GroupNoticeEvent(OneBotNoticeEvent):
    group_id: str
    user_id: str | None = None


class GroupUploadNoticeEvent(GroupNoticeEvent):
    file: dict[str, Any]


class GroupAdminNoticeEvent(GroupNoticeEvent):
    pass


class GroupMemberDecreaseNoticeEvent(GroupNoticeEvent):
    operator_id: str


class GroupMemberIncreaseNoticeEvent(GroupNoticeEvent):
    operator_id: str


class GroupBanNoticeEvent(GroupNoticeEvent):
    operator_id: str
    duration_seconds: int


class FriendAddNoticeEvent(OneBotNoticeEvent):
    user_id: str


class GroupRecallNoticeEvent(GroupNoticeEvent):
    operator_id: str
    message_id: str


class FriendRecallNoticeEvent(OneBotNoticeEvent):
    user_id: str
    message_id: str


class GroupCardNoticeEvent(GroupNoticeEvent):
    card_old: str
    card_new: str


class GroupEssenceNoticeEvent(GroupNoticeEvent):
    message_id: str
    sender_id: str
    operator_id: str


class GroupEmojiLikeNoticeEvent(GroupNoticeEvent):
    message_id: str
    likes: tuple[dict[str, Any], ...] = ()


class PokeNoticeEvent(OneBotNoticeEvent):
    user_id: str
    target_id: str
    group_id: str | None = None


class GroupHonorNoticeEvent(GroupNoticeEvent):
    honor_type: str | None = None


class GroupLuckyKingNoticeEvent(GroupNoticeEvent):
    target_id: str


class GroupTitleNoticeEvent(GroupNoticeEvent):
    title: str


class GroupNameNoticeEvent(GroupNoticeEvent):
    name_new: str


class GroupGrayTipNoticeEvent(GroupNoticeEvent):
    message_id: str
    business_id: str | None = None
    content: str


class ProfileLikeNoticeEvent(OneBotNoticeEvent):
    operator_id: str
    operator_nick: str | None = None
    times: int


class InputStatusNoticeEvent(OneBotNoticeEvent):
    user_id: str
    group_id: str | None = None
    status_text: str
    event_type: int | None = None


class BotOfflineNoticeEvent(OneBotNoticeEvent):
    user_id: str
    tag: str | None = None
    message: str | None = None


class OneBotRequestEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    timestamp: datetime
    request_type: str
    sub_type: str | None = None
    user_id: str | None = None
    group_id: str | None = None
    request_id: str | None = None
    comment: str | None = None
    flag: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class FriendRequestEvent(OneBotRequestEvent):
    pass


class GroupRequestEvent(OneBotRequestEvent):
    pass


class OneBotMetaEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    timestamp: datetime
    meta_event_type: str
    sub_type: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class HeartbeatEvent(OneBotMetaEvent):
    status: dict[str, Any]
    interval_ms: int


class LifecycleEvent(OneBotMetaEvent):
    pass


class UnknownOneBotEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str | None
    timestamp: datetime | None
    post_type: str | None
    event_type: str | None
    payload: dict[str, Any] = Field(default_factory=dict)


OneBotEvent = (
    InboundMessageEvent
    | MessageSentEvent
    | OneBotNoticeEvent
    | OneBotRequestEvent
    | OneBotMetaEvent
    | UnknownOneBotEvent
)


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
