from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx

from nekograph.config import Settings
from nekograph.web import create_dashboard_app


def dashboard_settings(tmp_path: Path) -> Settings:
    return Settings(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        tool_execution_ledger_path=tmp_path / "tool-executions.sqlite",
        model_profiles_path=tmp_path / "model-profiles.sqlite",
        scheduled_tasks_path=tmp_path / "scheduled-tasks.sqlite",
        onebot_action_ledger_path=tmp_path / "onebot-actions.sqlite",
        tool_sandbox_path=tmp_path / "tool-sandbox",
        dashboard_port=0,
        access_token=None,
    )


@asynccontextmanager
async def dashboard_client(tmp_path: Path) -> AsyncGenerator[httpx.AsyncClient]:
    app = create_dashboard_app(dashboard_settings(tmp_path))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client,
    ):
        yield client


async def test_dashboard_status_tools_config_and_chat(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        dashboard = await client.get("/")
        status = await client.get("/api/status")
        tools = await client.get("/api/tools")
        config = await client.get("/api/config")
        bots = await client.get("/api/onebot/bots")
        actions = await client.get("/api/onebot/actions")
        reply = await client.post(
            "/api/chat/browser-test/messages",
            json={"text": "hello dashboard"},
        )
        history = await client.get("/api/chat/browser-test/messages")
        reset = await client.post("/api/chat/browser-test/reset")

    assert dashboard.status_code == 200
    assert "NekoGraph Console" in dashboard.text
    assert status.status_code == 200
    status_data = cast(dict[str, Any], status.json())
    assert status_data["model"]["model"] == "fake"
    assert status_data["model_profile_count"] == 0
    assert status_data["tool_count"] == 2
    assert status_data["connected_bot_count"] == 0
    assert bots.status_code == 200
    assert bots.json() == []
    assert actions.status_code == 200
    assert actions.json() == []

    assert tools.status_code == 200
    tool_data = cast(list[dict[str, Any]], tools.json())
    assert {item["name"] for item in tool_data} == {
        "get_current_time",
        "write_demo_file",
    }
    assert all("handler" not in item for item in tool_data)

    assert config.status_code == 200
    config_data = cast(dict[str, Any], config.json())
    assert config_data["onebot"]["access_token_configured"] is False
    assert "access_token" not in config_data["onebot"]
    assert "api_key" not in str(config_data)
    assert config_data["onebot"]["action_max_concurrency"] == 16

    assert reply.status_code == 200
    assert reply.json()["content"] == "Fake response turn 1: hello dashboard"
    assert history.status_code == 200
    history_data = cast(list[dict[str, Any]], history.json())
    assert [(item["role"], item["content"]) for item in history_data] == [
        ("user", "hello dashboard"),
        ("assistant", "Fake response turn 1: hello dashboard"),
    ]
    assert reply.json()["response_time_ms"] >= 0
    assert history_data[1]["response_time_ms"] == reply.json()["response_time_ms"]
    assert reset.status_code == 200
    assert "reset" in reset.json()["content"].lower()


async def test_dashboard_scheduled_task_lifecycle(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        handlers = await client.get("/api/scheduled-task-handlers")
        created = await client.post(
            "/api/scheduled-tasks",
            json={
                "name": "诊断任务",
                "handler_name": "core.diagnostic",
                "schedule_kind": "interval",
                "interval_seconds": 3600,
                "timezone": "Asia/Shanghai",
                "payload": {"source": "test"},
                "enabled": True,
            },
        )
        task_id = created.json()["task_id"]
        listed = await client.get("/api/scheduled-tasks")
        run = await client.post(f"/api/scheduled-tasks/{task_id}/run")
        history = await client.get(f"/api/scheduled-tasks/{task_id}/runs")
        deleted = await client.delete(f"/api/scheduled-tasks/{task_id}")

    assert handlers.status_code == 200
    assert "core.diagnostic" in handlers.json()
    assert "core.onebot_send" in handlers.json()
    assert created.status_code == 201
    assert listed.status_code == 200
    assert listed.json()[0]["handler_name"] == "core.diagnostic"
    assert run.status_code == 200
    assert history.status_code == 200
    assert history.json()[0]["status"] == "completed"
    assert deleted.status_code == 204


async def test_dashboard_bulk_model_import_activation_and_delete(tmp_path: Path) -> None:
    profiles = [
        {
            "name": "Primary",
            "model": "model-a",
            "base_url": "https://provider-a.example/v1",
            "api_key": "secret-a",
            "temperature": 0.1,
            "timeout_seconds": 20,
        },
        {
            "name": "Backup",
            "model": "model-b",
            "base_url": "https://provider-b.example/v1",
            "api_key": "secret-b",
            "temperature": 0.2,
            "timeout_seconds": 30,
        },
    ]

    async with dashboard_client(tmp_path) as client:
        imported = await client.post("/api/models/import", json={"profiles": profiles})
        imported_data = cast(list[dict[str, Any]], imported.json())
        primary_id = str(
            next(item["profile_id"] for item in imported_data if item["name"] == "Primary")
        )
        activated = await client.post(f"/api/models/{primary_id}/activate")
        listed = await client.get("/api/models")
        rejected_delete = await client.delete(f"/api/models/{primary_id}")
        fallback = await client.post("/api/models/environment/activate")
        deleted = await client.delete(f"/api/models/{primary_id}")

    assert imported.status_code == 200
    assert len(imported_data) == 2
    assert all("api_key" not in item for item in imported_data)
    assert "secret-a" not in imported.text
    assert activated.status_code == 200
    assert activated.json()["profile_id"] == primary_id
    listed_data = cast(list[dict[str, Any]], listed.json())
    assert next(item for item in listed_data if item["profile_id"] == primary_id)[
        "active"
    ] is True
    assert rejected_delete.status_code == 409
    assert fallback.status_code == 200
    assert fallback.json()["source"] == "environment"
    assert deleted.status_code == 204


async def test_dashboard_rejects_invalid_or_duplicate_bulk_import(tmp_path: Path) -> None:
    valid = {
        "name": "Primary",
        "model": "model-a",
        "base_url": "https://provider.example/v1",
        "api_key": "secret",
    }

    async with dashboard_client(tmp_path) as client:
        first = await client.post("/api/models/import", json={"profiles": [valid]})
        duplicate = await client.post(
            "/api/models/import",
            json={"profiles": [{**valid, "name": "primary"}]},
        )
        invalid = await client.post(
            "/api/models/import",
            json={"profiles": [{**valid, "base_url": "ftp://invalid"}]},
        )
        listed = await client.get("/api/models")

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert invalid.status_code == 422
    assert len(listed.json()) == 1


async def test_dashboard_edits_active_model_without_exposing_api_key(
    tmp_path: Path,
) -> None:
    original = {
        "name": "Primary",
        "model": "model-a",
        "base_url": "https://provider.example/v1",
        "api_key": "secret",
        "temperature": 0.1,
        "timeout_seconds": 20,
    }
    changes = {
        "name": "Primary edited",
        "model": "model-b",
        "base_url": "https://updated.example/v1",
        "temperature": 0.6,
        "timeout_seconds": 45,
    }

    async with dashboard_client(tmp_path) as client:
        imported = await client.post("/api/models/import", json={"profiles": [original]})
        profile_id = str(imported.json()[0]["profile_id"])
        await client.post(f"/api/models/{profile_id}/activate")
        updated = await client.put(f"/api/models/{profile_id}", json=changes)
        status = await client.get("/api/status")

    assert updated.status_code == 200
    assert updated.json()["name"] == "Primary edited"
    assert updated.json()["active"] is True
    assert "api_key" not in updated.json()
    assert "secret" not in updated.text
    assert status.json()["model"] == {
        "profile_id": profile_id,
        "name": "Primary edited",
        "model": "model-b",
        "base_url": "https://updated.example/v1",
        "source": "profile",
    }


async def test_dashboard_deletes_one_conversation_checkpoint(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        first = await client.post(
            "/api/chat/conversation-a/messages", json={"text": "first"}
        )
        second = await client.post(
            "/api/chat/conversation-b/messages", json={"text": "second"}
        )
        deleted = await client.delete("/api/chat/conversation-a")
        first_history = await client.get("/api/chat/conversation-a/messages")
        second_history = await client.get("/api/chat/conversation-b/messages")

    assert first.status_code == 200
    assert second.status_code == 200
    assert deleted.status_code == 204
    assert first_history.json() == []
    assert [item["content"] for item in second_history.json()] == [
        "second",
        "Fake response turn 1: second",
    ]


async def test_dashboard_model_connection_test_does_not_switch_active_model(
    tmp_path: Path,
) -> None:
    profile = {
        "name": "Unactivated",
        "model": "model-a",
        "base_url": "http://127.0.0.1:1/v1",
        "api_key": "secret",
    }

    async with dashboard_client(tmp_path) as client:
        imported = await client.post("/api/models/import", json={"profiles": [profile]})
        profile_id = str(imported.json()[0]["profile_id"])
        tested = await client.post(f"/api/models/{profile_id}/test")
        status = await client.get("/api/status")

    assert tested.status_code == 502
    assert "secret" not in tested.text
    assert status.json()["model"]["source"] == "environment"


async def test_dashboard_rejects_invalid_conversation_id(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        response = await client.post(
            "/api/chat/not allowed/messages",
            json={"text": "hello"},
        )
        missing_api = await client.get("/api/not-found")

    assert response.status_code == 422
    assert missing_api.status_code == 404
