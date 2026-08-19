from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict

from nekograph.agent import FakeChatModel, LangGraphRuntime
from nekograph.application.conversation import ConversationResolver
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.models import (
    Actor,
    Chat,
    ChatKind,
    ConversationRef,
    InboundMessageEvent,
    MessageSegment,
    RunContext,
)
from nekograph.tools import (
    ToolDefinition,
    ToolExecutionContext,
    ToolRegistry,
    ToolRisk,
    build_core_tool_registry,
)
from nekograph.tools.models import JsonValue


def context(thread_id: str = "conversation-1", actor_id: str = "20001") -> RunContext:
    conversation = ConversationRef(conversation_id=thread_id, thread_id=thread_id)
    return RunContext(
        run_id="run-1",
        bot_id="10000",
        actor=Actor(user_id=actor_id, display_name="Alice"),
        chat=Chat(kind=ChatKind.PRIVATE, chat_id=actor_id),
        conversation=conversation,
    )


async def test_multiturn_state_survives_runtime_restart(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite"
    first_model = FakeChatModel()

    async with LangGraphRuntime.open(checkpoint_path=checkpoint, model=first_model) as runtime:
        first = await runtime.respond(context(), "first")
        second = await runtime.respond(context(), "second")

    restarted_model = FakeChatModel()
    async with LangGraphRuntime.open(checkpoint_path=checkpoint, model=restarted_model) as runtime:
        third = await runtime.respond(context(), "third")

    assert first == "Fake response turn 1: first"
    assert second == "Fake response turn 2: second"
    assert third == "Fake response turn 3: third"
    assert restarted_model.received_snapshots[0] == (
        ("human", "first"),
        ("ai", "Fake response turn 1: first"),
        ("human", "second"),
        ("ai", "Fake response turn 2: second"),
        ("human", "third"),
    )


async def test_reset_deletes_only_selected_thread(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite"
    model = FakeChatModel()
    other = context("conversation-2")

    async with LangGraphRuntime.open(checkpoint_path=checkpoint, model=model) as runtime:
        await runtime.respond(context(), "before reset")
        await runtime.respond(other, "other thread")
        await runtime.reset(context().conversation)
        after_reset = await runtime.respond(context(), "after reset")
        other_second = await runtime.respond(other, "still present")

    assert after_reset == "Fake response turn 1: after reset"
    assert other_second == "Fake response turn 2: still present"


async def test_model_boundary_contains_no_onebot_payload(tmp_path: Path) -> None:
    model = FakeChatModel()

    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite", model=model
    ) as runtime:
        await runtime.respond(context(), "safe internal text")

    snapshot = model.received_snapshots[0]
    serialized = repr(snapshot)
    assert "post_type" not in serialized
    assert "raw_message" not in serialized
    assert snapshot == (("human", "safe internal text"),)


async def test_reset_command_clears_real_langgraph_checkpoint(
    tmp_path: Path,
    event_factory: Callable[[str], InboundMessageEvent],
) -> None:
    model = FakeChatModel()
    event = event_factory("private_message.json")
    reset_message = event.message.model_copy(
        update={"segments": (MessageSegment.text("/reset"),), "plain_text": "/reset"}
    )
    reset_event = event.model_copy(update={"message": reset_message})

    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite", model=model
    ) as runtime:
        application = MessageApplication(
            runtime=runtime,
            conversations=ConversationResolver(),
            wakeup=WakeupPolicy(),
        )
        first = await application.handle(event)
        reset = await application.handle(reset_event)
        after_reset = await application.handle(event)

    assert first is not None
    assert reset is not None
    assert after_reset is not None
    assert model.calls == 2
    assert "reset" in "".join(segment.text_content for segment in reset.segments).lower()
    assert "turn 1" in "".join(segment.text_content for segment in after_reset.segments)


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


async def echo_tool(arguments: BaseModel) -> JsonValue:
    parsed = EchoArgs.model_validate(arguments)
    return {"echo": parsed.text}


async def test_safe_tool_call_runs_through_langgraph_and_returns_to_model(
    tmp_path: Path,
) -> None:
    model = FakeChatModel(
        scripted_responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-1",
                        "name": "echo",
                        "args": {"text": "hello"},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="The tool returned hello."),
        ]
    )
    registry = ToolRegistry(
        (
            ToolDefinition(
                name="echo",
                description="Echo text.",
                args_schema=EchoArgs,
                handler=echo_tool,
                source="test",
                risk=ToolRisk.SAFE,
            ),
        )
    )

    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=registry,
    ) as runtime:
        response = await runtime.respond(context(), "use echo")

    assert response == "The tool returned hello."
    assert model.calls == 2
    assert model.received_tools[0][0]["function"]["name"] == "echo"
    second_snapshot = model.received_snapshots[1]
    assert [message_type for message_type, _ in second_snapshot] == ["human", "ai", "tool"]
    tool_messages = [item for item in second_snapshot if item[0] == "tool"]
    assert '"success":true' in tool_messages[0][1]
    assert '"echo":"hello"' in tool_messages[0][1]


def tool_call_response(*, risk_name: str = "echo") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": risk_name,
                "args": {"text": "hello"},
                "type": "tool_call",
            }
        ],
    )


def sensitive_registry(handler: Callable[[BaseModel], JsonValue]) -> ToolRegistry:
    async def invoke(arguments: BaseModel) -> JsonValue:
        return handler(arguments)

    return ToolRegistry(
        (
            ToolDefinition(
                name="echo",
                description="Approval-gated echo.",
                args_schema=EchoArgs,
                handler=invoke,
                source="test",
                risk=ToolRisk.SENSITIVE,
            ),
        )
    )


async def test_sensitive_tool_interrupts_before_execution_and_approve_is_idempotent(
    tmp_path: Path,
) -> None:
    calls = 0

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        parsed = EchoArgs.model_validate(arguments)
        return {"echo": parsed.text}

    model = FakeChatModel(
        scripted_responses=[tool_call_response(), AIMessage(content="approved result")]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        execution_ledger_path=tmp_path / "executions.sqlite",
        model=model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
    ) as runtime:
        interrupted = await runtime.respond(context(), "use sensitive echo")
        assert calls == 0

        approved = await runtime.approve(context(), "approval-1")
        duplicate = await runtime.approve(context(), "approval-1")

    assert "/approve approval-1" in interrupted
    assert "/deny approval-1" in interrupted
    assert approved == "approved result"
    assert "No pending" in duplicate
    assert calls == 1
    assert model.calls == 2


async def test_sensitive_tool_deny_resumes_without_execution(tmp_path: Path) -> None:
    calls = 0

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    model = FakeChatModel(
        scripted_responses=[tool_call_response(), AIMessage(content="request denied")]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
    ) as runtime:
        await runtime.respond(context(), "use sensitive echo")
        denied = await runtime.deny(context(), "approval-1")

    assert denied == "request denied"
    assert calls == 0
    assert '"code":"approval_denied"' in model.received_snapshots[1][-1][1]


async def test_approval_rejects_wrong_user_and_conversation(tmp_path: Path) -> None:
    calls = 0

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"echo": "hello"}

    model = FakeChatModel(
        scripted_responses=[tool_call_response(), AIMessage(content="approved")]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
    ) as runtime:
        await runtime.respond(context(), "use sensitive echo")

        wrong_user = await runtime.approve(context(actor_id="20002"), "approval-1")
        wrong_id = await runtime.approve(context(), "approval-wrong")
        wrong_conversation = await runtime.approve(
            context(thread_id="conversation-2"), "approval-1"
        )
        assert calls == 0
        approved = await runtime.approve(context(), "approval-1")

    assert "Only the user" in wrong_user
    assert "does not match" in wrong_id
    assert "No pending" in wrong_conversation
    assert approved == "approved"
    assert calls == 1


async def test_pending_approval_survives_runtime_restart(tmp_path: Path) -> None:
    checkpoint = tmp_path / "checkpoints.sqlite"
    ledger = tmp_path / "executions.sqlite"
    calls = 0

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"echo": "hello"}

    first_model = FakeChatModel(scripted_responses=[tool_call_response()])
    async with LangGraphRuntime.open(
        checkpoint_path=checkpoint,
        execution_ledger_path=ledger,
        model=first_model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
    ) as runtime:
        interrupted = await runtime.respond(context(), "use sensitive echo")

    restarted_model = FakeChatModel(scripted_responses=[AIMessage(content="resumed")])
    async with LangGraphRuntime.open(
        checkpoint_path=checkpoint,
        execution_ledger_path=ledger,
        model=restarted_model,
        tools=sensitive_registry(handler),
    ) as runtime:
        resumed = await runtime.approve(context(), "approval-1")

    assert "/approve approval-1" in interrupted
    assert resumed == "resumed"
    assert calls == 1
    assert restarted_model.calls == 1


async def test_reset_clears_pending_approval(tmp_path: Path) -> None:
    calls = 0

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"echo": "hello"}

    model = FakeChatModel(scripted_responses=[tool_call_response()])
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
    ) as runtime:
        await runtime.respond(context(), "use sensitive echo")
        await runtime.reset(context().conversation)
        result = await runtime.approve(context(), "approval-1")

    assert "No pending" in result
    assert calls == 0


async def test_expired_approval_resumes_as_denied_without_execution(tmp_path: Path) -> None:
    calls = 0
    now = [datetime(2026, 8, 19, tzinfo=UTC)]

    def clock() -> datetime:
        return now[0]

    def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"echo": "hello"}

    model = FakeChatModel(
        scripted_responses=[tool_call_response(), AIMessage(content="expired")]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=sensitive_registry(handler),
        approval_id_factory=lambda: "approval-1",
        approval_ttl_seconds=1,
        clock=clock,
    ) as runtime:
        await runtime.respond(context(), "use sensitive echo")
        now[0] += timedelta(seconds=2)
        result = await runtime.approve(context(), "approval-1")

    assert result == "expired"
    assert calls == 0
    assert '"code":"approval_expired"' in model.received_snapshots[1][-1][1]


async def test_new_message_first_resolves_expired_approval_without_appending_input(
    tmp_path: Path,
) -> None:
    now = [datetime(2026, 8, 19, tzinfo=UTC)]

    def clock() -> datetime:
        return now[0]

    model = FakeChatModel(
        scripted_responses=[tool_call_response(), AIMessage(content="approval expired")]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=sensitive_registry(lambda arguments: {"unexpected": True}),
        approval_id_factory=lambda: "approval-1",
        approval_ttl_seconds=1,
        clock=clock,
    ) as runtime:
        await runtime.respond(context(), "original request")
        now[0] += timedelta(seconds=2)

        result = await runtime.respond(context(), "new message")

    assert result == "approval expired"
    assert model.calls == 2
    assert all(content != "new message" for _, content in model.received_snapshots[1])


async def test_dangerous_tool_is_denied_unless_explicitly_enabled(tmp_path: Path) -> None:
    calls = 0

    async def handler(arguments: BaseModel) -> JsonValue:
        nonlocal calls
        calls += 1
        return {"unexpected": True}

    registry = ToolRegistry(
        (
            ToolDefinition(
                name="dangerous_echo",
                description="Dangerous test tool.",
                args_schema=EchoArgs,
                handler=handler,
                source="test",
                risk=ToolRisk.DANGEROUS,
            ),
        )
    )
    model = FakeChatModel(
        scripted_responses=[
            tool_call_response(risk_name="dangerous_echo"),
            AIMessage(content="dangerous tools disabled"),
        ]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=registry,
        tool_context=ToolExecutionContext(allow_dangerous=False),
    ) as runtime:
        result = await runtime.respond(context(), "use dangerous echo")

    assert result == "dangerous tools disabled"
    assert calls == 0
    assert '"code":"dangerous_disabled"' in model.received_snapshots[1][-1][1]

    enabled_model = FakeChatModel(
        scripted_responses=[
            tool_call_response(risk_name="dangerous_echo"),
            AIMessage(content="dangerous tool approved"),
        ]
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "enabled-checkpoints.sqlite",
        model=enabled_model,
        tools=registry,
        tool_context=ToolExecutionContext(allow_dangerous=True),
        approval_id_factory=lambda: "approval-dangerous",
    ) as runtime:
        interrupted = await runtime.respond(context(), "use dangerous echo")
        assert calls == 0
        approved = await runtime.approve(context(), "approval-dangerous")

    assert "[dangerous]" in interrupted
    assert approved == "dangerous tool approved"
    assert calls == 1


async def test_application_approval_flow_writes_only_after_explicit_command(
    tmp_path: Path,
    event_factory: Callable[[str], InboundMessageEvent],
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
                        "args": {"path": "demo/result.txt", "content": "approved"},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="file written"),
        ]
    )
    event = event_factory("private_message.json")
    approve_message = event.message.model_copy(
        update={
            "segments": (MessageSegment.text("/approve approval-write"),),
            "plain_text": "/approve approval-write",
        }
    )
    approve_event = event.model_copy(update={"message": approve_message})

    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        model=model,
        tools=build_core_tool_registry(sandbox),
        tool_context=ToolExecutionContext(
            permissions=frozenset({"demo_file:write"})
        ),
        approval_id_factory=lambda: "approval-write",
    ) as runtime:
        application = MessageApplication(
            runtime=runtime,
            conversations=ConversationResolver(),
            wakeup=WakeupPolicy(),
        )
        interrupted = await application.handle(event)
        assert interrupted is not None
        assert not (sandbox / "demo" / "result.txt").exists()

        completed = await application.handle(approve_event)

    assert completed is not None
    interrupted_text = "".join(segment.text_content for segment in interrupted.segments)
    completed_text = "".join(segment.text_content for segment in completed.segments)
    assert "/approve approval-write" in interrupted_text
    assert completed_text == "file written"
    assert (sandbox / "demo" / "result.txt").read_text(encoding="utf-8") == "approved"
