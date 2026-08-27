from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from nekograph.biliwatch.models import BiliDynamic
from nekograph.config import Settings
from nekograph.knowledge.parsers import ParsedDocument
from nekograph.web import create_dashboard_app


def dashboard_settings(tmp_path: Path) -> Settings:
    return Settings(
        checkpoint_path=tmp_path / "checkpoints.sqlite",
        knowledge_path=tmp_path / "knowledge.sqlite",
        knowledge_models_path=tmp_path / "knowledge-models.json",
        tool_execution_ledger_path=tmp_path / "tool-executions.sqlite",
        model_profiles_path=tmp_path / "model-profiles.sqlite",
        scheduled_tasks_path=tmp_path / "scheduled-tasks.sqlite",
        onebot_action_ledger_path=tmp_path / "onebot-actions.sqlite",
        biliwatch_path=tmp_path / "biliwatch.sqlite",
        biliwatch_config_path=tmp_path / "biliwatch-config.json",
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
    assert status_data["tool_count"] == 3
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
        "search_knowledge",
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


async def test_dashboard_knowledge_base_full_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_parse_url(url: str, *, timeout_seconds: float = 20.0) -> ParsedDocument:
        del timeout_seconds
        return ParsedDocument(
            title="网页资料",
            source=url,
            source_url=url,
            content="# 活动资料\n\n泠鸢参加了专题音乐活动。",
        )

    monkeypatch.setattr("nekograph.knowledge.service.parse_url", fake_parse_url)
    async with dashboard_client(tmp_path) as client:
        initial = await client.get("/api/knowledge-bases")
        created = await client.post(
            "/api/knowledge-bases", json={"name": "interviews", "description": "采访资料"}
        )
        upload = await client.post(
            "/api/knowledge-bases/yousa/documents/upload",
            files={"file": ("profile.md", "# 人物资料\n\n泠鸢是音乐创作者。", "text/markdown")},
        )
        imported_url = await client.post(
            "/api/knowledge-bases/yousa/documents/url",
            json={"url": "https://example.test/yousa"},
        )
        documents = await client.get("/api/knowledge-bases/yousa/documents")
        search = await client.post(
            "/api/knowledge-bases/yousa/search",
            json={"query": "音乐创作者", "limit": 5},
        )
        empty = await client.post(
            "/api/knowledge-bases/yousa/search",
            json={"query": "不存在的资料", "limit": 5},
        )
        rebuilt = await client.post("/api/knowledge-bases/yousa/rebuild")
        document_id = upload.json()["document_id"]
        deleted_document = await client.delete(
            f"/api/knowledge-bases/yousa/documents/{document_id}"
        )
        after_delete = await client.get("/api/knowledge-bases/yousa/documents")
        deleted_collection = await client.delete("/api/knowledge-bases/interviews")

    assert initial.status_code == 200
    assert any(item["name"] == "yousa" for item in initial.json())
    assert created.status_code == 201
    assert upload.status_code == 201
    assert upload.json()["chunk_count"] == 1
    assert imported_url.status_code == 201
    assert imported_url.json()["source_url"] == "https://example.test/yousa"
    assert len(documents.json()) == 2
    assert search.status_code == 200 and search.json()["found"] is True
    assert "音乐创作者" in search.json()["results"][0]["content"]
    assert empty.status_code == 200 and empty.json() == {"found": False, "results": []}
    assert rebuilt.json() == {"status": "completed"}
    assert deleted_document.status_code == 204
    assert len(after_delete.json()) == 1
    assert deleted_collection.status_code == 204


async def test_dashboard_knowledge_upload_and_url_failures(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        wrong_type = await client.post(
            "/api/knowledge-bases/yousa/documents/upload",
            files={"file": ("image.png", b"not-an-image", "image/png")},
        )
        empty_file = await client.post(
            "/api/knowledge-bases/yousa/documents/upload",
            files={"file": ("empty.txt", b"", "text/plain")},
        )
        bad_url = await client.post(
            "/api/knowledge-bases/yousa/documents/url",
            json={"url": "not-a-url"},
        )

    assert wrong_type.status_code == 415
    assert empty_file.status_code == 400
    assert bad_url.status_code == 400


async def test_dashboard_knowledge_model_config_is_redacted(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        models = await client.get("/api/knowledge/models")
        config = await client.get("/api/config")
        invalid_test = await client.post(
            "/api/knowledge/models/test",
            json={"kind": "invalid", "base_url": "x", "model": "m", "api_key": "secret"},
        )

    assert models.status_code == 200
    assert models.json()["embedding"]["configured"] is False
    assert models.json()["reranker"]["configured"] is False
    assert config.status_code == 200
    assert config.json()["knowledge"]["embedding"]["configured"] is False
    assert "api_key" not in str(config.json())
    assert invalid_test.status_code == 422


async def test_dashboard_imports_and_deletes_knowledge_model_config(tmp_path: Path) -> None:
    async with dashboard_client(tmp_path) as client:
        imported = await client.post(
            "/api/knowledge/models",
            json={
                "kind": "embedding",
                "base_url": "https://provider.test/v1",
                "model": "embedding-model",
                "api_key": "secret-key",
            },
        )
        listed = await client.get("/api/knowledge/models")
        deleted = await client.delete("/api/knowledge/models/embedding")
        after_delete = await client.get("/api/knowledge/models")

    assert imported.status_code == 201
    assert imported.json()["configured"] is True
    assert "secret-key" not in imported.text
    assert listed.json()["embedding"]["model"] == "embedding-model"
    assert deleted.status_code == 204
    assert after_delete.json()["embedding"]["configured"] is False


async def test_dashboard_tests_embedding_and_reranker_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_embed(self: object, texts: list[str]) -> list[list[float]]:
        del self, texts
        return [[0.1, 0.2]]

    async def fake_rerank(
        self: object, query: str, documents: list[str], limit: int
    ) -> list[float]:
        del self, query, documents, limit
        return [0.8]

    monkeypatch.setattr("nekograph.knowledge.embedding.OpenAICompatibleEmbedding.embed", fake_embed)
    monkeypatch.setattr("nekograph.knowledge.reranker.OpenAICompatibleReranker.rerank", fake_rerank)
    async with dashboard_client(tmp_path) as client:
        embedding = await client.post(
            "/api/knowledge/models/test",
            json={
                "kind": "embedding",
                "base_url": "https://provider.test/v1",
                "model": "embedding-model",
                "api_key": "secret-key",
            },
        )
        reranker = await client.post(
            "/api/knowledge/models/test",
            json={
                "kind": "reranker",
                "base_url": "https://provider.test/v1",
                "model": "rerank-model",
                "api_key": "secret-key",
            },
        )

    assert embedding.json() == {"ok": True, "kind": "embedding", "dimension": 2}
    assert reranker.json() == {"ok": True, "kind": "reranker", "score_count": 1}


async def test_dashboard_biliwatch_config_subscription_and_delivery_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_name(self: object, uid: str) -> str:
        del self, uid
        return "Test UP"

    async def fake_dynamics(self: object, uid: str) -> list[BiliDynamic]:
        del self, uid
        return []

    monkeypatch.setattr("nekograph.biliwatch.client.BilibiliClient.creator_name", fake_name)
    monkeypatch.setattr("nekograph.biliwatch.client.BilibiliClient.recent_dynamics", fake_dynamics)
    async with dashboard_client(tmp_path) as client:
        initial_config = await client.get("/api/biliwatch/config")
        updated_config = await client.put(
            "/api/biliwatch/config",
            json={
                "admins": ["20001"],
                "poll_interval_seconds": 120,
                "sessdata": "secret-sessdata",
                "bili_jct": None,
                "dede_user_id": None,
            },
        )
        created = await client.post(
            "/api/biliwatch/subscriptions",
            json={
                "bot_id": "10000",
                "group_id": "30001",
                "uid": "123",
                "watch_dynamic": True,
                "watch_live": False,
                "at_all_dynamic": False,
                "at_all_live": False,
                "filter_forward": True,
                "enabled": True,
            },
        )
        listed = await client.get("/api/biliwatch/subscriptions")
        deliveries = await client.get("/api/biliwatch/deliveries")
        deleted = await client.delete(
            f"/api/biliwatch/subscriptions/{created.json()['subscription_id']}"
        )

    assert initial_config.status_code == 200
    assert initial_config.json()["sessdata_configured"] is False
    assert updated_config.status_code == 200
    assert "secret-sessdata" not in updated_config.text
    assert updated_config.json()["sessdata_configured"] is True
    assert created.status_code == 201
    assert created.json()["uname"] == "Test UP"
    assert listed.status_code == 200 and len(listed.json()) == 1
    assert deliveries.status_code == 200 and deliveries.json() == []
    assert deleted.status_code == 204


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
    assert next(item for item in listed_data if item["profile_id"] == primary_id)["active"] is True
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
        first = await client.post("/api/chat/conversation-a/messages", json={"text": "first"})
        second = await client.post("/api/chat/conversation-b/messages", json={"text": "second"})
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
