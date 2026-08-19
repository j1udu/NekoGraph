from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from nekograph.models import Chat, ChatKind, OutboundMessage
from nekograph.protocols.onebot_v11.actions import (
    ActionRisk,
    ActionStatus,
    BotUnavailableError,
    OneBotActionFailedError,
    OneBotActionLedger,
    OneBotActionTransport,
    OneBotConnectionHub,
    OneBotManagementService,
    OneBotMessageSender,
    OneBotQueryService,
    ScheduledOneBotMessage,
)


class FakeConnection:
    def __init__(
        self,
        response: dict[str, object] | None = None,
        *,
        failure: Exception | None = None,
        delay: float = 0,
        on_active: Callable[[int], None] | None = None,
    ) -> None:
        self.response = response or {"status": "ok", "retcode": 0, "data": {}}
        self.failure = failure
        self.delay = delay
        self.on_active = on_active
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.closed_reason: str | None = None
        self.pending_error: Exception | None = None
        self.active = 0

    async def call_action(self, action: str, params: dict[str, object]) -> dict[str, object]:
        self.calls.append((action, params))
        self.active += 1
        if self.on_active is not None:
            self.on_active(self.active)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            if self.failure is not None:
                raise self.failure
            return self.response
        finally:
            self.active -= 1

    def fail_pending(self, error: Exception | None = None) -> None:
        self.pending_error = error

    async def close(self, reason: str) -> None:
        self.closed_reason = reason


async def test_connection_hub_fails_fast_and_replacement_is_identity_safe() -> None:
    hub = OneBotConnectionHub()
    with pytest.raises(BotUnavailableError, match="not connected"):
        await hub.call("10000", "get_login_info", {})

    first = FakeConnection()
    second = FakeConnection()
    await hub.attach("10000", first)
    await hub.attach("10000", second)
    await hub.detach("10000", first)

    await hub.call("10000", "get_login_info", {})

    assert first.closed_reason == "replaced by a newer OneBot connection"
    assert first.pending_error is not None
    assert second.calls == [("get_login_info", {})]
    assert hub.is_connected("10000")


async def test_message_sender_serializes_same_bot_and_redacts_ledger(
    tmp_path: Path,
) -> None:
    maximum_active = 0

    def observe(active: int) -> None:
        nonlocal maximum_active
        maximum_active = max(maximum_active, active)

    connection = FakeConnection(
        {"status": "ok", "retcode": 0, "data": {"message_id": 9001}},
        delay=0.01,
        on_active=observe,
    )
    hub = OneBotConnectionHub()
    await hub.attach("10000", connection)
    async with OneBotActionLedger.open(tmp_path / "actions.sqlite") as ledger:
        sender = OneBotMessageSender(
            OneBotActionTransport(hub, ledger), minimum_interval_seconds=0
        )
        message = OutboundMessage.text(
            bot_id="10000",
            chat=Chat(kind=ChatKind.GROUP, chat_id="30001"),
            content="private message body must not enter ledger",
        )

        receipts = await asyncio.gather(
            sender.send(message, source="test"),
            sender.send(message, source="test"),
        )
        records = await ledger.recent()

    assert maximum_active == 1
    assert [receipt.message_id for receipt in receipts] == ["9001", "9001"]
    assert len(records) == 2
    assert all(record.status is ActionStatus.COMPLETED for record in records)
    assert all(record.risk is ActionRisk.SENSITIVE for record in records)
    assert all("private message body" not in str(record.target_summary) for record in records)
    assert records[0].target_summary == {
        "group_id": 30001,
        "message_segment_types": ["text"],
        "message_segment_count": 1,
    }


async def test_failed_action_is_recorded_without_losing_original_error(tmp_path: Path) -> None:
    failure = OneBotActionFailedError("set_group_ban", 1400, "permission denied")
    connection = FakeConnection(failure=failure)
    hub = OneBotConnectionHub()
    await hub.attach("10000", connection)
    async with OneBotActionLedger.open(tmp_path / "actions.sqlite") as ledger:
        transport = OneBotActionTransport(hub, ledger)
        with pytest.raises(OneBotActionFailedError) as captured:
            await transport.call(
                bot_id="10000",
                action="set_group_ban",
                params={"group_id": 30001, "user_id": 20001, "duration": 60},
                risk=ActionRisk.DANGEROUS,
                source="group_policy",
            )
        records = await ledger.recent()

    assert captured.value is failure
    assert records[0].status is ActionStatus.FAILED
    assert records[0].retcode == 1400
    assert records[0].source == "group_policy"


async def test_query_and_management_services_use_only_documented_fields(
    tmp_path: Path,
) -> None:
    connection = FakeConnection()
    hub = OneBotConnectionHub()
    await hub.attach("10000", connection)
    async with OneBotActionLedger.open(tmp_path / "actions.sqlite") as ledger:
        transport = OneBotActionTransport(hub, ledger)
        queries = OneBotQueryService(transport)
        management = OneBotManagementService(transport)

        await queries.group_member_info("10000", "30001", "20001", no_cache=True)
        await management.mute_member(
            "10000", "30001", "20001", 60, source="test_policy"
        )
        await management.kick_member(
            "10000", "30001", "20001", reject_add_request=True, source="test_policy"
        )
        await management.handle_group_request(
            "10000", "request-flag", "add", False, reason="blocked", source="test_policy"
        )

    assert connection.calls == [
        (
            "get_group_member_info",
            {"group_id": 30001, "user_id": 20001, "no_cache": True},
        ),
        (
            "set_group_ban",
            {"group_id": 30001, "user_id": 20001, "duration": 60},
        ),
        (
            "set_group_kick",
            {"group_id": 30001, "user_id": 20001, "reject_add_request": True},
        ),
        (
            "set_group_add_request",
            {"flag": "request-flag", "sub_type": "add", "approve": False, "reason": "blocked"},
        ),
    ]


def test_scheduled_onebot_message_rejects_raw_actions_and_invalid_ids() -> None:
    valid = {
        "bot_id": "10000",
        "chat_kind": "group",
        "chat_id": "30001",
        "text": "scheduled diagnostic",
    }
    assert ScheduledOneBotMessage.model_validate(valid).outbound().chat.kind is ChatKind.GROUP

    with pytest.raises(ValidationError):
        ScheduledOneBotMessage.model_validate({**valid, "action": "set_group_ban"})
    with pytest.raises(ValidationError):
        ScheduledOneBotMessage.model_validate({**valid, "chat_id": "not-numeric"})
