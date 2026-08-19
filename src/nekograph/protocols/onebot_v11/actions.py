"""Connection-independent OneBot action transport and typed service facade."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field

from nekograph.models import Chat, ChatKind, OutboundMessage


class OneBotActionError(RuntimeError):
    """Base error for a failed OneBot action."""


class BotUnavailableError(OneBotActionError):
    """The requested bot does not have an active reverse WebSocket connection."""


class OneBotActionTimeoutError(OneBotActionError):
    """OneBot did not return an action response before the timeout."""


class OneBotActionFailedError(OneBotActionError):
    def __init__(self, action: str, retcode: object, wording: str | None = None) -> None:
        detail = f", wording={wording!r}" if wording else ""
        super().__init__(f"OneBot action failed: action={action}, retcode={retcode!r}{detail}")
        self.action = action
        self.retcode = retcode
        self.wording = wording


class ActionRisk(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    DANGEROUS = "dangerous"


class ActionStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ActionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    action_id: str
    bot_id: str
    action: str
    risk: ActionRisk
    source: str
    correlation_id: str | None = None
    target_summary: dict[str, Any]
    status: ActionStatus
    retcode: int | None = None
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    duration_ms: int | None = None


class SendReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    message_id: str
    chat_kind: ChatKind
    chat_id: str
    sent_at: datetime


class ScheduledOneBotMessage(BaseModel):
    """Strict payload accepted by the built-in scheduled send handler."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    bot_id: str = Field(pattern=r"^\d+$")
    chat_kind: ChatKind
    chat_id: str = Field(pattern=r"^\d+$")
    text: str = Field(min_length=1, max_length=10_000)

    def outbound(self) -> OutboundMessage:
        return OutboundMessage.text(
            bot_id=self.bot_id,
            chat=Chat(kind=self.chat_kind, chat_id=self.chat_id),
            content=self.text,
        )


class OneBotActionConnection(Protocol):
    async def call_action(self, action: str, params: dict[str, object]) -> dict[str, object]: ...

    def fail_pending(self, error: Exception | None = None) -> None: ...

    async def close(self, reason: str) -> None: ...


class OneBotConnectionHub:
    """Tracks the current reverse WebSocket connection for each QQ bot."""

    def __init__(self) -> None:
        self._connections: dict[str, OneBotActionConnection] = {}
        self._connected_at: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def attach(self, bot_id: str, connection: OneBotActionConnection) -> None:
        previous: OneBotActionConnection | None
        async with self._lock:
            previous = self._connections.get(bot_id)
            self._connections[bot_id] = connection
            self._connected_at[bot_id] = datetime.now(UTC)
        if previous is not None and previous is not connection:
            previous.fail_pending(ConnectionError("OneBot connection replaced"))
            await previous.close("replaced by a newer OneBot connection")

    async def detach(self, bot_id: str, connection: OneBotActionConnection) -> None:
        async with self._lock:
            if self._connections.get(bot_id) is connection:
                self._connections.pop(bot_id, None)
                self._connected_at.pop(bot_id, None)

    async def call(
        self, bot_id: str, action: str, params: dict[str, object]
    ) -> dict[str, object]:
        async with self._lock:
            connection = self._connections.get(bot_id)
        if connection is None:
            raise BotUnavailableError(f"OneBot bot is not connected: {bot_id}")
        return await connection.call_action(action, params)

    def connected_bots(self) -> tuple[dict[str, object], ...]:
        return tuple(
            {
                "bot_id": bot_id,
                "connected_at": self._connected_at[bot_id].isoformat(),
            }
            for bot_id in sorted(self._connections)
        )

    def is_connected(self, bot_id: str) -> bool:
        return bot_id in self._connections


class OneBotActionLedger:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path) -> AsyncGenerator[OneBotActionLedger]:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as connection:
            connection.row_factory = aiosqlite.Row
            ledger = cls(connection)
            await ledger.setup()
            yield ledger

    async def setup(self) -> None:
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS onebot_actions (
                action_id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                action TEXT NOT NULL,
                risk TEXT NOT NULL,
                source TEXT NOT NULL,
                correlation_id TEXT,
                target_summary TEXT NOT NULL,
                status TEXT NOT NULL,
                retcode INTEGER,
                error TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                duration_ms INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_onebot_actions_started
                ON onebot_actions(started_at DESC);
            """
        )
        await self._connection.commit()

    async def start(self, record: ActionRecord) -> None:
        await self._connection.execute(
            """
            INSERT INTO onebot_actions(
                action_id, bot_id, action, risk, source, correlation_id,
                target_summary, status, started_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.action_id,
                record.bot_id,
                record.action,
                record.risk.value,
                record.source,
                record.correlation_id,
                json.dumps(record.target_summary, ensure_ascii=False, separators=(",", ":")),
                record.status.value,
                record.started_at.isoformat(),
            ),
        )
        await self._connection.commit()

    async def finish(
        self,
        action_id: str,
        *,
        status: ActionStatus,
        retcode: int | None,
        error: str | None,
        finished_at: datetime,
        duration_ms: int,
    ) -> None:
        await self._connection.execute(
            """
            UPDATE onebot_actions
            SET status=?, retcode=?, error=?, finished_at=?, duration_ms=?
            WHERE action_id=?
            """,
            (status.value, retcode, error, finished_at.isoformat(), duration_ms, action_id),
        )
        await self._connection.commit()

    async def recent(self, limit: int = 100) -> list[ActionRecord]:
        cursor = await self._connection.execute(
            "SELECT * FROM onebot_actions ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        return [self._record(row) for row in await cursor.fetchall()]

    @staticmethod
    def _record(row: aiosqlite.Row) -> ActionRecord:
        summary = json.loads(str(row["target_summary"]))
        return ActionRecord(
            action_id=str(row["action_id"]),
            bot_id=str(row["bot_id"]),
            action=str(row["action"]),
            risk=ActionRisk(str(row["risk"])),
            source=str(row["source"]),
            correlation_id=row["correlation_id"],
            target_summary=cast(dict[str, Any], summary),
            status=ActionStatus(str(row["status"])),
            retcode=row["retcode"],
            error=row["error"],
            started_at=datetime.fromisoformat(str(row["started_at"])),
            finished_at=(
                datetime.fromisoformat(str(row["finished_at"]))
                if row["finished_at"] is not None
                else None
            ),
            duration_ms=row["duration_ms"],
        )


class OneBotActionTransport:
    def __init__(
        self,
        hub: OneBotConnectionHub,
        ledger: OneBotActionLedger,
        *,
        max_concurrency: int = 16,
    ) -> None:
        self._hub = hub
        self._ledger = ledger
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def call(
        self,
        *,
        bot_id: str,
        action: str,
        params: dict[str, object],
        risk: ActionRisk,
        source: str,
        correlation_id: str | None = None,
    ) -> object:
        action_id = uuid4().hex
        started = datetime.now(UTC)
        record = ActionRecord(
            action_id=action_id,
            bot_id=bot_id,
            action=action,
            risk=risk,
            source=source,
            correlation_id=correlation_id,
            target_summary=_summarize_params(params),
            status=ActionStatus.RUNNING,
            started_at=started,
        )
        await self._ledger.start(record)
        retcode: int | None = None
        error: str | None = None
        status = ActionStatus.COMPLETED
        try:
            async with self._semaphore:
                response = await self._hub.call(bot_id, action, params)
            retcode_value = response.get("retcode")
            retcode = retcode_value if isinstance(retcode_value, int) else None
            return response.get("data")
        except Exception as exc:
            status = ActionStatus.FAILED
            error = str(exc)
            if isinstance(exc, OneBotActionFailedError) and isinstance(exc.retcode, int):
                retcode = exc.retcode
            raise
        finally:
            finished = datetime.now(UTC)
            try:
                await self._ledger.finish(
                    action_id,
                    status=status,
                    retcode=retcode,
                    error=error,
                    finished_at=finished,
                    duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
                )
            except Exception:
                if error is None:
                    raise


class OneBotMessageSender:
    def __init__(
        self,
        transport: OneBotActionTransport,
        *,
        minimum_interval_seconds: float = 0.5,
    ) -> None:
        self._transport = transport
        self._minimum_interval = minimum_interval_seconds
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_sent: dict[str, float] = {}

    async def send(
        self,
        message: OutboundMessage,
        *,
        source: str,
        correlation_id: str | None = None,
    ) -> SendReceipt:
        action, params = outbound_to_action(message)
        lock = self._locks.setdefault(message.bot_id, asyncio.Lock())
        async with lock:
            elapsed = time.monotonic() - self._last_sent.get(message.bot_id, 0.0)
            delay = self._minimum_interval - elapsed
            if delay > 0:
                await asyncio.sleep(delay)
            data = await self._transport.call(
                bot_id=message.bot_id,
                action=action,
                params=params,
                risk=ActionRisk.SENSITIVE,
                source=source,
                correlation_id=correlation_id,
            )
            self._last_sent[message.bot_id] = time.monotonic()
        if not isinstance(data, dict):
            raise OneBotActionError("OneBot send action returned no message_id")
        typed_data = cast(dict[str, object], data)
        message_id = typed_data.get("message_id")
        if message_id is None:
            raise OneBotActionError("OneBot send action returned no message_id")
        return SendReceipt(
            bot_id=message.bot_id,
            message_id=str(message_id),
            chat_kind=message.chat.kind,
            chat_id=message.chat.chat_id,
            sent_at=datetime.now(UTC),
        )


def outbound_to_action(message: OutboundMessage) -> tuple[str, dict[str, object]]:
    target_id = _numeric_id(message.chat.chat_id, "chat_id")
    segments: list[dict[str, object]] = []
    if message.reply_to is not None:
        segments.append({"type": "reply", "data": {"id": message.reply_to}})
    segments.extend({"type": segment.kind, "data": segment.data} for segment in message.segments)
    if message.chat.kind is ChatKind.PRIVATE:
        return "send_private_msg", {"user_id": target_id, "message": segments}
    return "send_group_msg", {"group_id": target_id, "message": segments}


class OneBotQueryService:
    def __init__(self, transport: OneBotActionTransport) -> None:
        self._transport = transport

    async def login_info(self, bot_id: str) -> object:
        return await self._call(bot_id, "get_login_info", {})

    async def friend_list(self, bot_id: str) -> object:
        return await self._call(bot_id, "get_friend_list", {})

    async def group_list(self, bot_id: str) -> object:
        return await self._call(bot_id, "get_group_list", {})

    async def group_info(self, bot_id: str, group_id: str, *, no_cache: bool = False) -> object:
        return await self._call(
            bot_id,
            "get_group_info",
            {"group_id": _numeric_id(group_id, "group_id"), "no_cache": no_cache},
        )

    async def group_member_list(self, bot_id: str, group_id: str) -> object:
        return await self._call(
            bot_id, "get_group_member_list", {"group_id": _numeric_id(group_id, "group_id")}
        )

    async def group_member_info(
        self, bot_id: str, group_id: str, user_id: str, *, no_cache: bool = False
    ) -> object:
        return await self._call(
            bot_id,
            "get_group_member_info",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "no_cache": no_cache,
            },
        )

    async def message(self, bot_id: str, message_id: str) -> object:
        return await self._call(
            bot_id, "get_msg", {"message_id": _numeric_id(message_id, "message_id")}
        )

    async def _call(self, bot_id: str, action: str, params: dict[str, object]) -> object:
        return await self._transport.call(
            bot_id=bot_id,
            action=action,
            params=params,
            risk=ActionRisk.SAFE,
            source="query",
        )


class OneBotManagementService:
    def __init__(self, transport: OneBotActionTransport) -> None:
        self._transport = transport

    async def recall_message(self, bot_id: str, message_id: str, *, source: str) -> None:
        await self._call(
            bot_id, "delete_msg", {"message_id": _numeric_id(message_id, "message_id")}, source
        )

    async def mute_member(
        self, bot_id: str, group_id: str, user_id: str, duration_seconds: int, *, source: str
    ) -> None:
        if duration_seconds < 0:
            raise ValueError("duration_seconds must be non-negative")
        await self._call(
            bot_id,
            "set_group_ban",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "duration": duration_seconds,
            },
            source,
        )

    async def set_whole_group_mute(
        self, bot_id: str, group_id: str, enabled: bool, *, source: str
    ) -> None:
        await self._call(
            bot_id,
            "set_group_whole_ban",
            {"group_id": _numeric_id(group_id, "group_id"), "enable": enabled},
            source,
        )

    async def kick_member(
        self,
        bot_id: str,
        group_id: str,
        user_id: str,
        *,
        reject_add_request: bool = False,
        source: str,
    ) -> None:
        await self._call(
            bot_id,
            "set_group_kick",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "reject_add_request": reject_add_request,
            },
            source,
        )

    async def set_group_admin(
        self, bot_id: str, group_id: str, user_id: str, enabled: bool, *, source: str
    ) -> None:
        await self._call(
            bot_id,
            "set_group_admin",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "enable": enabled,
            },
            source,
        )

    async def set_group_card(
        self, bot_id: str, group_id: str, user_id: str, card: str, *, source: str
    ) -> None:
        await self._call(
            bot_id,
            "set_group_card",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "card": card,
            },
            source,
        )

    async def set_group_name(
        self, bot_id: str, group_id: str, group_name: str, *, source: str
    ) -> None:
        await self._call(
            bot_id,
            "set_group_name",
            {"group_id": _numeric_id(group_id, "group_id"), "group_name": group_name},
            source,
        )

    async def set_special_title(
        self,
        bot_id: str,
        group_id: str,
        user_id: str,
        title: str,
        *,
        duration_seconds: int = -1,
        source: str,
    ) -> None:
        await self._call(
            bot_id,
            "set_group_special_title",
            {
                "group_id": _numeric_id(group_id, "group_id"),
                "user_id": _numeric_id(user_id, "user_id"),
                "special_title": title,
                "duration": duration_seconds,
            },
            source,
        )

    async def handle_friend_request(
        self,
        bot_id: str,
        flag: str,
        approve: bool,
        *,
        remark: str = "",
        source: str,
    ) -> None:
        await self._call(
            bot_id,
            "set_friend_add_request",
            {"flag": flag, "approve": approve, "remark": remark},
            source,
        )

    async def handle_group_request(
        self,
        bot_id: str,
        flag: str,
        sub_type: str,
        approve: bool,
        *,
        reason: str = "",
        source: str,
    ) -> None:
        if sub_type not in {"add", "invite"}:
            raise ValueError("sub_type must be 'add' or 'invite'")
        await self._call(
            bot_id,
            "set_group_add_request",
            {"flag": flag, "sub_type": sub_type, "approve": approve, "reason": reason},
            source,
        )

    async def _call(
        self, bot_id: str, action: str, params: dict[str, object], source: str
    ) -> None:
        await self._transport.call(
            bot_id=bot_id,
            action=action,
            params=params,
            risk=ActionRisk.DANGEROUS,
            source=source,
        )


def _numeric_id(value: str, field: str) -> int:
    if not value.isdigit():
        raise OneBotActionError(f"OneBot {field} must be numeric: {value}")
    return int(value)


def _summarize_params(params: Mapping[str, object]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key in (
        "user_id",
        "group_id",
        "message_id",
        "duration",
        "enable",
        "approve",
        "sub_type",
        "reject_add_request",
    ):
        if key in params:
            summary[key] = params[key]
    message = params.get("message")
    if isinstance(message, list):
        typed_message = cast(list[object], message)
        segment_types: list[object] = []
        for item in typed_message:
            if isinstance(item, dict):
                segment_types.append(cast(dict[str, object], item).get("type"))
        summary["message_segment_types"] = segment_types
        summary["message_segment_count"] = len(typed_message)
    return summary
