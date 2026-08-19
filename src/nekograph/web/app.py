"""FastAPI boundary for the NekoGraph management dashboard."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as ApiPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from nekograph import __version__
from nekograph.agent import ModelProfileInput, ModelProfileUpdate
from nekograph.agent.openai_compatible import ModelProviderError
from nekograph.agent.profiles import (
    ActiveModelProfileError,
    DuplicateModelProfileError,
    ModelProfileNotFoundError,
    ModelProfileView,
    profile_view,
)
from nekograph.bootstrap import RuntimeResources, open_runtime_resources
from nekograph.config import Settings
from nekograph.models import MessageSegment
from nekograph.protocols.onebot_v11.actions import ActionRecord
from nekograph.protocols.web_chat import WebChatAdapter
from nekograph.scheduling import ScheduledTaskInput, SchedulingError
from nekograph.web.logs import DashboardLogHandler

logger = logging.getLogger(__name__)
ConversationId = Annotated[
    str,
    ApiPath(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$"),
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=20_000)


class ChatResponse(BaseModel):
    message_id: str
    content: str
    response_time_ms: int | None = None


class HistoryMessage(BaseModel):
    role: str
    content: str
    tool_calls: list[dict[str, Any]] = Field(default_factory=lambda: [])
    response_time_ms: int | None = None


class ModelImportRequest(BaseModel):
    profiles: list[ModelProfileInput] = Field(min_length=1, max_length=50)


class ScheduledTaskRequest(ScheduledTaskInput):
    pass


@dataclass(frozen=True, slots=True)
class DashboardContext:
    resources: RuntimeResources
    chat: WebChatAdapter
    logs: DashboardLogHandler
    started_at: datetime


def _context(request: Request) -> DashboardContext:
    return cast(DashboardContext, request.app.state.context)


def _outbound_text(segments: tuple[MessageSegment, ...]) -> str:
    return "".join(segment.text_content for segment in segments)


def _message_content(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content)


def _history_message(
    message: BaseMessage, response_time_ms: int | None = None
) -> HistoryMessage:
    if isinstance(message, HumanMessage):
        return HistoryMessage(role="user", content=_message_content(message))
    if isinstance(message, ToolMessage):
        return HistoryMessage(role="tool", content=_message_content(message))
    if isinstance(message, AIMessage):
        return HistoryMessage(
            role="assistant",
            content=_message_content(message),
            tool_calls=[dict(item) for item in message.tool_calls],
            response_time_ms=response_time_ms,
        )
    return HistoryMessage(role=message.type, content=_message_content(message))


def create_dashboard_app(
    settings: Settings | None = None,
    *,
    shared_resources: RuntimeResources | None = None,
) -> FastAPI:
    configured = settings or Settings()
    logs = DashboardLogHandler()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        root = logging.getLogger()
        root.addHandler(logs)
        try:
            if shared_resources is not None:
                app.state.context = DashboardContext(
                    resources=shared_resources,
                    chat=WebChatAdapter(
                        shared_resources.application(conversation_namespace="web:v1")
                    ),
                    logs=logs,
                    started_at=datetime.now(UTC),
                )
                logger.info("dashboard_runtime_shared")
                yield
            else:
                async with open_runtime_resources(configured) as resources:
                    app.state.context = DashboardContext(
                        resources=resources,
                        chat=WebChatAdapter(
                            resources.application(conversation_namespace="web:v1")
                        ),
                        logs=logs,
                        started_at=datetime.now(UTC),
                    )
                    logger.info("dashboard_runtime_started")
                    yield
        finally:
            root.removeHandler(logs)

    app = FastAPI(
        title="NekoGraph Dashboard API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, Any]:
        context = _context(request)
        profiles = await context.resources.model_profiles.list()
        active = context.resources.models.active_info
        connected_bots = context.resources.onebot_hub.connected_bots()
        return {
            "version": __version__,
            "started_at": context.started_at,
            "uptime_seconds": max(
                0, int((datetime.now(UTC) - context.started_at).total_seconds())
            ),
            "model": {
                "profile_id": active.profile_id,
                "name": active.name,
                "model": active.model,
                "base_url": active.base_url,
                "source": active.source,
            },
            "model_profile_count": len(profiles),
            "tool_count": len(context.resources.tools.definitions()),
            "checkpoint": "sqlite",
            "gateway": "dashboard_only",
            "connected_bot_count": len(connected_bots),
            "scheduled_task_count": len(await context.resources.scheduler.list()),
        }

    @app.get("/api/onebot/bots")
    async def connected_onebot_bots(request: Request) -> list[dict[str, object]]:
        return list(_context(request).resources.onebot_hub.connected_bots())

    @app.get("/api/onebot/actions", response_model=list[ActionRecord])
    async def recent_onebot_actions(
        request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[ActionRecord]:
        return await _context(request).resources.onebot_actions.recent(limit)

    @app.get("/api/scheduled-task-handlers")
    async def scheduled_task_handlers(request: Request) -> list[str]:
        return list(_context(request).resources.scheduler.handler_names())

    @app.get("/api/scheduled-tasks")
    async def list_scheduled_tasks(request: Request) -> list[dict[str, Any]]:
        tasks = await _context(request).resources.scheduler.list()
        items: list[dict[str, Any]] = []
        for task in tasks:
            item = task.model_dump(mode="json")
            live_next = _context(request).resources.scheduler.next_run_at(task.task_id)
            item["next_run_at"] = live_next.isoformat() if live_next else None
            items.append(item)
        return items

    @app.post("/api/scheduled-tasks", status_code=201)
    async def create_scheduled_task(
        body: ScheduledTaskRequest, request: Request
    ) -> dict[str, Any]:
        try:
            task = await _context(request).resources.scheduler.create(body)
        except SchedulingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.put("/api/scheduled-tasks/{task_id}")
    async def update_scheduled_task(
        task_id: str, body: ScheduledTaskRequest, request: Request
    ) -> dict[str, Any]:
        try:
            task = await _context(request).resources.scheduler.update(task_id, body)
        except SchedulingError as exc:
            status = 404 if "not found" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        return task.model_dump(mode="json")

    @app.delete("/api/scheduled-tasks/{task_id}", status_code=204)
    async def delete_scheduled_task(task_id: str, request: Request) -> None:
        try:
            await _context(request).resources.scheduler.delete(task_id)
        except SchedulingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/scheduled-tasks/{task_id}/run")
    async def run_scheduled_task(task_id: str, request: Request) -> dict[str, str]:
        try:
            await _context(request).resources.scheduler.run_now(task_id)
        except SchedulingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"status": "completed"}

    @app.get("/api/scheduled-tasks/{task_id}/runs")
    async def list_scheduled_task_runs(
        task_id: str, request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50
    ) -> list[dict[str, Any]]:
        try:
            runs = await _context(request).resources.scheduler.runs(task_id, limit)
        except SchedulingError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [run.model_dump(mode="json") for run in runs]

    @app.post("/api/chat/{conversation_id}/messages", response_model=ChatResponse)
    async def send_chat_message(
        conversation_id: ConversationId, body: ChatRequest, request: Request
    ) -> ChatResponse:
        context = _context(request)
        conversation = context.chat.conversation(conversation_id)
        previous = await context.resources.runtime.history(conversation)
        turn_index = sum(isinstance(message, HumanMessage) for message in previous) + 1
        started = datetime.now(UTC)
        response = await context.chat.send(conversation_id, body.text)
        elapsed_ms = max(0, int((datetime.now(UTC) - started).total_seconds() * 1000))
        if response is None:
            raise HTTPException(status_code=500, detail="chat produced no response")
        await context.resources.conversation_metadata.record_response_time(
            conversation_id, turn_index, elapsed_ms
        )
        return ChatResponse(
            message_id=response.reply_to or "",
            content=_outbound_text(response.segments),
            response_time_ms=elapsed_ms,
        )

    @app.get(
        "/api/chat/{conversation_id}/messages", response_model=list[HistoryMessage]
    )
    async def chat_history(
        conversation_id: ConversationId, request: Request
    ) -> list[HistoryMessage]:
        context = _context(request)
        conversation = context.chat.conversation(conversation_id)
        messages = await context.resources.runtime.history(conversation)
        response_times = await context.resources.conversation_metadata.response_times(
            conversation_id
        )
        turn_index = 0
        history: list[HistoryMessage] = []
        for message in messages:
            if isinstance(message, HumanMessage):
                turn_index += 1
            history.append(_history_message(message, response_times.get(turn_index)))
        return history

    @app.post("/api/chat/{conversation_id}/reset", response_model=ChatResponse)
    async def reset_chat(
        conversation_id: ConversationId, request: Request
    ) -> ChatResponse:
        context = _context(request)
        response = await context.chat.send(conversation_id, "/reset")
        if response is None:
            raise HTTPException(status_code=500, detail="reset produced no response")
        return ChatResponse(
            message_id=response.reply_to or "",
            content=_outbound_text(response.segments),
        )

    @app.delete("/api/chat/{conversation_id}", status_code=204)
    async def delete_chat(conversation_id: ConversationId, request: Request) -> None:
        context = _context(request)
        await context.resources.runtime.reset(context.chat.conversation(conversation_id))
        await context.resources.conversation_metadata.delete(conversation_id)

    @app.get("/api/models", response_model=list[ModelProfileView])
    async def list_models(request: Request) -> list[ModelProfileView]:
        profiles = await _context(request).resources.model_profiles.list()
        return [profile_view(profile) for profile in profiles]

    @app.post("/api/models/import", response_model=list[ModelProfileView])
    async def import_models(
        body: ModelImportRequest, request: Request
    ) -> list[ModelProfileView]:
        try:
            profiles = await _context(request).resources.model_profiles.create_many(
                body.profiles
            )
        except DuplicateModelProfileError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return [profile_view(profile) for profile in profiles]

    @app.post("/api/models/environment/activate")
    async def activate_environment_model(request: Request) -> dict[str, Any]:
        active = await _context(request).resources.models.activate(None)
        return {
            "profile_id": active.profile_id,
            "name": active.name,
            "model": active.model,
            "base_url": active.base_url,
            "source": active.source,
        }

    @app.post("/api/models/{profile_id}/activate")
    async def activate_model(profile_id: str, request: Request) -> dict[str, Any]:
        try:
            active = await _context(request).resources.models.activate(profile_id)
        except ModelProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {
            "profile_id": active.profile_id,
            "name": active.name,
            "model": active.model,
            "base_url": active.base_url,
            "source": active.source,
        }

    @app.post("/api/models/{profile_id}/test")
    async def test_model(profile_id: str, request: Request) -> dict[str, Any]:
        try:
            await _context(request).resources.models.test_profile(profile_id)
        except ModelProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ModelProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"ok": True, "message": "model connection succeeded"}

    @app.put("/api/models/{profile_id}", response_model=ModelProfileView)
    async def update_model(
        profile_id: str, body: ModelProfileUpdate, request: Request
    ) -> ModelProfileView:
        context = _context(request)
        try:
            profile = await context.resources.model_profiles.update(profile_id, body)
            if profile.active:
                await context.resources.models.activate(profile_id)
        except ModelProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except DuplicateModelProfileError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return profile_view(profile)

    @app.delete("/api/models/{profile_id}", status_code=204)
    async def delete_model(profile_id: str, request: Request) -> None:
        try:
            await _context(request).resources.model_profiles.delete(profile_id)
        except ModelProfileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ActiveModelProfileError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.get("/api/tools")
    async def tools(request: Request) -> list[dict[str, Any]]:
        return [
            {
                "name": definition.name,
                "description": definition.description,
                "source": definition.source,
                "risk": definition.risk,
                "timeout_seconds": definition.timeout_seconds,
                "required_permissions": sorted(definition.required_permissions),
            }
            for definition in _context(request).resources.tools.definitions()
        ]

    @app.get("/api/config")
    async def config(request: Request) -> dict[str, Any]:
        current = _context(request).resources.settings
        return {
            "onebot": {
                "host": current.host,
                "port": current.port,
                "path": current.websocket_path,
                "access_token_configured": bool(current.access_token),
                "action_timeout_seconds": current.action_timeout_seconds,
                "action_max_concurrency": current.onebot_action_max_concurrency,
                "send_min_interval_seconds": current.onebot_send_min_interval_seconds,
            },
            "dashboard": {
                "host": current.dashboard_host,
                "port": current.dashboard_port,
            },
            "agent": {
                "checkpoint_backend": "sqlite",
                "group_conversation_mode": current.group_conversation_mode,
                "group_wake_prefixes": current.group_wake_prefixes,
            },
            "tools": {
                "permissions": current.tool_permissions,
                "allow_dangerous": current.allow_dangerous_tools,
                "approval_ttl_seconds": current.tool_approval_ttl_seconds,
            },
        }

    @app.get("/api/logs")
    async def recent_logs(
        request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 100
    ) -> list[dict[str, Any]]:
        return _context(request).logs.recent(limit)

    static_dir = Path(__file__).with_name("static")
    assets_dir = static_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="dashboard-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def dashboard_spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API endpoint was not found")
        index = static_dir / "index.html"
        if not index.is_file():
            raise HTTPException(status_code=503, detail="dashboard frontend is not built")
        return FileResponse(index)

    return app
