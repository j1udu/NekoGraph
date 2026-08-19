from __future__ import annotations

from pathlib import Path

import aiosqlite

from nekograph.agent.ledger import ToolExecutionLedger
from nekograph.tools import ToolResult, ToolResultCode


async def test_execution_ledger_claim_is_durable_and_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "executions.sqlite"
    async with aiosqlite.connect(path) as connection:
        ledger = ToolExecutionLedger(connection)
        await ledger.setup()

        first = await ledger.claim(
            approval_id="approval-1",
            conversation_id="conversation-1",
            tool_name="write_demo_file",
        )
        duplicate = await ledger.claim(
            approval_id="approval-1",
            conversation_id="conversation-1",
            tool_name="write_demo_file",
        )
        await ledger.complete(
            "approval-1",
            ToolResult(
                tool_name="write_demo_file",
                code=ToolResultCode.SUCCESS,
                output={"path": "demo.txt"},
            ),
        )

    async with aiosqlite.connect(path) as connection:
        restarted = ToolExecutionLedger(connection)
        await restarted.setup()
        record = await restarted.get("approval-1")

    assert first
    assert not duplicate
    assert record is not None
    assert record.conversation_id == "conversation-1"
    assert record.tool_name == "write_demo_file"
    assert record.status == "completed"
    assert record.result is not None
    assert record.result.output == {"path": "demo.txt"}


async def test_execution_ledger_clears_only_selected_conversation(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "executions.sqlite") as connection:
        ledger = ToolExecutionLedger(connection)
        await ledger.setup()
        await ledger.claim(
            approval_id="approval-1",
            conversation_id="conversation-1",
            tool_name="tool",
        )
        await ledger.claim(
            approval_id="approval-2",
            conversation_id="conversation-2",
            tool_name="tool",
        )

        await ledger.clear_conversation("conversation-1")

        assert await ledger.get("approval-1") is None
        assert await ledger.get("approval-2") is not None
