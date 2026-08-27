"""Typed business models for Bilibili subscriptions and deliveries."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WatchType(StrEnum):
    DYNAMIC = "dynamic"
    LIVE = "live"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class BiliDynamic(BaseModel):
    model_config = ConfigDict(frozen=True)

    dynamic_id: str
    uid: str
    uname: str
    dynamic_type: str
    published_at: datetime
    text: str = ""
    image_urls: tuple[str, ...] = ()
    url: str
    original_uname: str | None = None
    original_type: str | None = None
    original_text: str | None = None

    @property
    def content_key(self) -> str:
        return f"dynamic:{self.dynamic_id}"


class BiliLiveStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    uid: str
    live_status: int
    room_id: str | None = None
    title: str = ""
    cover_url: str | None = None
    url: str | None = None
    area_name: str = ""

    @property
    def is_live(self) -> bool:
        return self.live_status == 1


class SubscriptionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bot_id: str = Field(pattern=r"^\d+$")
    group_id: str = Field(pattern=r"^\d+$")
    uid: str = Field(pattern=r"^\d+$")
    watch_dynamic: bool = True
    watch_live: bool = True
    at_all_dynamic: bool = False
    at_all_live: bool = False
    filter_forward: bool = False
    enabled: bool = True


class Subscription(SubscriptionInput):
    subscription_id: str
    uname: str
    last_dynamic_timestamp: int | None = None
    was_live: bool = False
    created_at: datetime
    updated_at: datetime


class StoredContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    content_key: str
    uid: str
    kind: WatchType
    published_at: datetime
    payload: dict[str, Any]
    discovered_at: datetime


class DeliveryRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivery_id: str
    subscription_id: str
    content_key: str
    bot_id: str
    group_id: str
    uid: str
    kind: WatchType
    status: DeliveryStatus
    attempts: int
    message_id: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    sent_at: datetime | None = None


class PendingDelivery(BaseModel):
    model_config = ConfigDict(frozen=True)

    delivery: DeliveryRecord
    subscription: Subscription
    content: StoredContent


class PollReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    checked_uids: int = 0
    discovered_contents: int = 0
    sent_deliveries: int = 0
    failed_deliveries: int = 0
    retried_deliveries: int = 0
