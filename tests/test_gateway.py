from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from socket import socket
from typing import cast

import pytest
from langchain_core.messages import AIMessage
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedError

from nekograph.agent import FakeChatModel, LangGraphRuntime
from nekograph.application.conversation import ConversationResolver
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.models import (
    Chat,
    ChatKind,
    ConversationRef,
    MessageSegment,
    OutboundMessage,
    RunContext,
)
from nekograph.protocols.onebot_v11.gateway import (
    OneBotActionError,
    ReverseWebSocketGateway,
    outbound_to_action,
)
from nekograph.tools import ToolExecutionContext, build_core_tool_registry

FIXTURES = Path(__file__).parent / "fixtures"


class EchoRuntime:
    async def respond(self, context: RunContext, text: str) -> str:
        return f"echo:{text}"

    async def reset(self, conversation: ConversationRef) -> None:
        return None

    async def approve(self, context: RunContext, approval_id: str) -> str:
        return f"approved:{approval_id}"

    async def deny(self, context: RunContext, approval_id: str) -> str:
        return f"denied:{approval_id}"


def make_gateway(*, token: str | None = None) -> ReverseWebSocketGateway:
    application = MessageApplication(
        runtime=EchoRuntime(),
        conversations=ConversationResolver(),
        wakeup=WakeupPolicy(),
    )
    return ReverseWebSocketGateway(
        application=application,
        host="127.0.0.1",
        port=0,
        path="/onebot/v11/ws",
        access_token=token,
        action_timeout_seconds=0.5,
    )


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def server_port(server_sockets: Iterable[socket]) -> int:
    address = next(iter(server_sockets)).getsockname()
    return cast(int, address[1])


async def receive_action(connection: ClientConnection) -> dict[str, object]:
    raw = await connection.recv()
    assert isinstance(raw, str)
    decoded = json.loads(raw)
    assert isinstance(decoded, dict)
    return cast(dict[str, object], decoded)


def test_group_outbound_message_becomes_onebot_action() -> None:
    message = OutboundMessage.text(
        bot_id="10000",
        chat=Chat(kind=ChatKind.GROUP, chat_id="30001"),
        content="group response",
        reply_to="102",
    )

    action, params = outbound_to_action(message)

    assert action == "send_group_msg"
    assert params == {
        "group_id": 30001,
        "message": [
            {"type": "reply", "data": {"id": "102"}},
            {"type": "text", "data": {"text": "group response"}},
        ],
    }


def test_non_numeric_onebot_target_is_rejected() -> None:
    message = OutboundMessage(
        bot_id="10000",
        chat=Chat(kind=ChatKind.PRIVATE, chat_id="not-a-qq-id"),
        segments=(MessageSegment.text("response"),),
    )

    with pytest.raises(OneBotActionError, match="numeric"):
        outbound_to_action(message)


async def test_reverse_websocket_event_action_and_echo_round_trip() -> None:
    gateway = make_gateway()

    async with gateway.run() as server:
        port = server_port(server.sockets)
        headers = {"X-Self-ID": "10000", "X-Client-Role": "Universal"}
        async with connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers
        ) as connection:
            await connection.send(fixture_text("private_message.json"))
            action = await receive_action(connection)

            assert action["action"] == "send_private_msg"
            params = cast(dict[str, object], action["params"])
            assert params["user_id"] == 20001
            assert params["message"] == [
                {"type": "reply", "data": {"id": "101"}},
                {"type": "text", "data": {"text": "echo:hello neko"}},
            ]

            await connection.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {"message_id": 9001},
                        "echo": action["echo"],
                    }
                )
            )


async def test_bad_payload_and_failed_action_do_not_close_gateway() -> None:
    gateway = make_gateway()

    async with gateway.run() as server:
        port = server_port(server.sockets)
        headers = {"X-Self-ID": "10000", "X-Client-Role": "Universal"}
        async with connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers
        ) as connection:
            await connection.send("not-json")
            await connection.send(fixture_text("private_message.json"))
            first_action = await receive_action(connection)
            await connection.send(
                json.dumps(
                    {
                        "status": "failed",
                        "retcode": 1400,
                        "data": None,
                        "echo": first_action["echo"],
                    }
                )
            )

            await connection.send(fixture_text("private_message.json"))
            second_action = await receive_action(connection)
            assert second_action["action"] == "send_private_msg"
            await connection.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": {},
                        "echo": second_action["echo"],
                    }
                )
            )


async def test_gateway_rejects_invalid_access_token() -> None:
    gateway = make_gateway(token="secret")

    async with gateway.run() as server:
        port = server_port(server.sockets)
        headers = {
            "X-Self-ID": "10000",
            "X-Client-Role": "Universal",
            "Authorization": "Bearer wrong",
        }
        async with connect(
            f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers
        ) as connection:
            try:
                await connection.recv()
            except ConnectionClosedError:
                assert connection.close_code == 1008
            else:
                raise AssertionError("gateway accepted an invalid access token")


async def test_reverse_websocket_interrupt_approve_and_sensitive_tool_round_trip(
    tmp_path: Path,
) -> None:
    sandbox = tmp_path / "sandbox"
    model = FakeChatModel(
        scripted_responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-write",
                        "name": "write_demo_file",
                        "args": {"path": "gateway/result.txt", "content": "approved"},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="gateway flow complete"),
        ]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        execution_ledger_path=tmp_path / "executions.sqlite",
        model=model,
        tools=build_core_tool_registry(sandbox),
        tool_context=ToolExecutionContext(
            permissions=frozenset({"demo_file:write"})
        ),
        approval_id_factory=lambda: "approval-gateway",
    ) as runtime:
        application = MessageApplication(
            runtime=runtime,
            conversations=ConversationResolver(),
            wakeup=WakeupPolicy(),
        )
        gateway = ReverseWebSocketGateway(
            application=application,
            host="127.0.0.1",
            port=0,
            path="/onebot/v11/ws",
            access_token=None,
            action_timeout_seconds=0.5,
        )
        async with gateway.run() as server:
            port = server_port(server.sockets)
            headers = {"X-Self-ID": "10000", "X-Client-Role": "Universal"}
            async with connect(
                f"ws://127.0.0.1:{port}/onebot/v11/ws", additional_headers=headers
            ) as connection:
                await connection.send(fixture_text("private_message.json"))
                approval_action = await receive_action(connection)
                approval_params = cast(dict[str, object], approval_action["params"])
                approval_segments = cast(list[dict[str, object]], approval_params["message"])
                approval_text = cast(dict[str, str], approval_segments[-1]["data"])["text"]

                assert "/approve approval-gateway" in approval_text
                assert not (sandbox / "gateway" / "result.txt").exists()
                await connection.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {},
                            "echo": approval_action["echo"],
                        }
                    )
                )

                approve_payload = json.loads(fixture_text("private_message.json"))
                assert isinstance(approve_payload, dict)
                approve_payload["message_id"] = 103
                approve_payload["message"] = [
                    {"type": "text", "data": {"text": "/approve approval-gateway"}}
                ]
                approve_payload["raw_message"] = "/approve approval-gateway"
                await connection.send(json.dumps(approve_payload))
                completed_action = await receive_action(connection)
                completed_params = cast(dict[str, object], completed_action["params"])
                completed_segments = cast(list[dict[str, object]], completed_params["message"])
                completed_text = cast(dict[str, str], completed_segments[-1]["data"])["text"]

                assert completed_text == "gateway flow complete"
                assert (sandbox / "gateway" / "result.txt").read_text() == "approved"
                await connection.send(
                    json.dumps(
                        {
                            "status": "ok",
                            "retcode": 0,
                            "data": {},
                            "echo": completed_action["echo"],
                        }
                    )
                )
