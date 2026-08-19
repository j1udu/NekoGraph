"""Composition root shared by protocol adapters and the dashboard."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, Iterable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from nekograph.agent import (
    ActiveModelInfo,
    ChatModel,
    FakeChatModel,
    LangGraphRuntime,
    ModelController,
    ModelHandle,
    ModelProfileStore,
    OpenAICompatibleChatModel,
    OpenAICompatibleConfig,
)
from nekograph.application.commands import CommandRegistry, register_core_commands
from nekograph.application.conversation import ConversationResolver
from nekograph.application.conversation_metadata import ConversationMetadataStore
from nekograph.application.events import EventRouter
from nekograph.application.scheduler import ConversationScheduler
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.config import ModelBackend, Settings
from nekograph.plugins import Plugin, PluginManager
from nekograph.protocols.onebot_v11.actions import (
    OneBotActionLedger,
    OneBotActionTransport,
    OneBotConnectionHub,
    OneBotManagementService,
    OneBotMessageSender,
    OneBotQueryService,
    ScheduledOneBotMessage,
)
from nekograph.scheduling import SchedulerRuntime, TaskHandlerContext, TaskHandlerRegistry
from nekograph.tools import ToolExecutionContext, ToolRegistry, build_core_tool_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_configured_model(
    settings: Settings,
) -> AsyncGenerator[tuple[ChatModel, ActiveModelInfo]]:
    if settings.model_backend is ModelBackend.FAKE:
        logger.warning("fake_model_active")
        yield FakeChatModel(), ActiveModelInfo(
            profile_id=None,
            name="Environment fallback",
            model="fake",
            base_url=None,
            source="environment",
        )
        return

    assert settings.llm_model is not None
    assert settings.llm_api_key is not None
    config = OpenAICompatibleConfig(
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        temperature=settings.llm_temperature,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    async with OpenAICompatibleChatModel(config) as model:
        yield model, ActiveModelInfo(
            profile_id=None,
            name="Environment fallback",
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            source="environment",
        )


@dataclass(frozen=True, slots=True)
class RuntimeResources:
    settings: Settings
    runtime: LangGraphRuntime
    models: ModelController
    model_profiles: ModelProfileStore
    tools: ToolRegistry
    commands: CommandRegistry
    plugins: PluginManager
    conversation_metadata: ConversationMetadataStore
    scheduler: SchedulerRuntime
    onebot_hub: OneBotConnectionHub
    onebot_sender: OneBotMessageSender
    onebot_queries: OneBotQueryService
    onebot_management: OneBotManagementService
    onebot_actions: OneBotActionLedger
    events: EventRouter

    def application(self, *, conversation_namespace: str = "qq:v1") -> MessageApplication:
        return MessageApplication(
            runtime=self.runtime,
            conversations=ConversationResolver(
                self.settings.group_conversation_mode,
                namespace=conversation_namespace,
            ),
            wakeup=WakeupPolicy(self.settings.group_wake_prefixes),
            scheduler=ConversationScheduler(),
            commands=self.commands,
        )


@asynccontextmanager
async def open_runtime_resources(
    settings: Settings,
    *,
    plugins: Iterable[Plugin] = (),
) -> AsyncGenerator[RuntimeResources]:
    tools = build_core_tool_registry(settings.tool_sandbox_path)
    commands = CommandRegistry()
    for name in ("/help", "/status", "/reset", "/approve", "/deny"):
        commands.reserve(name)
    plugin_manager = PluginManager(tool_registry=tools, command_registry=commands)
    await plugin_manager.load_all(plugins)
    tool_context = ToolExecutionContext(
        permissions=frozenset(settings.tool_permissions),
        allow_dangerous=settings.allow_dangerous_tools,
    )
    task_handlers = TaskHandlerRegistry()
    onebot_hub = OneBotConnectionHub()
    events = EventRouter()

    async def diagnostic_handler(context: TaskHandlerContext) -> None:
        logger.info(
            "scheduled_task_diagnostic",
            extra={"task_id": context.task.task_id, "run_id": context.run_id},
        )

    task_handlers.register("core.diagnostic", diagnostic_handler)
    async with (
        open_configured_model(settings) as fallback,
        ModelProfileStore.open(settings.model_profiles_path) as model_profiles,
        ConversationMetadataStore.open(
            settings.conversation_metadata_path
        ) as conversation_metadata,
        OneBotActionLedger.open(settings.onebot_action_ledger_path) as onebot_actions,
    ):
        onebot_transport = OneBotActionTransport(
            onebot_hub,
            onebot_actions,
            max_concurrency=settings.onebot_action_max_concurrency,
        )
        onebot_sender = OneBotMessageSender(
            onebot_transport,
            minimum_interval_seconds=settings.onebot_send_min_interval_seconds,
        )
        onebot_queries = OneBotQueryService(onebot_transport)
        onebot_management = OneBotManagementService(onebot_transport)

        async def onebot_send_handler(context: TaskHandlerContext) -> None:
            payload = ScheduledOneBotMessage.model_validate(dict(context.payload))
            await onebot_sender.send(
                payload.outbound(),
                source="scheduled_task",
                correlation_id=context.run_id,
            )

        task_handlers.register("core.onebot_send", onebot_send_handler)
        async with SchedulerRuntime.open(
            settings.scheduled_tasks_path,
            task_handlers,
            max_concurrency=settings.scheduled_task_max_concurrency,
        ) as scheduler:
            fallback_model, fallback_info = fallback
            models = ModelController(
                store=model_profiles,
                fallback=ModelHandle(fallback_model),
                fallback_info=fallback_info,
            )
            await models.initialize()
            try:
                async with LangGraphRuntime.open(
                    checkpoint_path=settings.checkpoint_path,
                    model=models,
                    tools=tools,
                    tool_context=tool_context,
                    execution_ledger_path=settings.tool_execution_ledger_path,
                    approval_ttl_seconds=settings.tool_approval_ttl_seconds,
                ) as runtime:
                    register_core_commands(commands, runtime)
                    yield RuntimeResources(
                        settings=settings,
                        runtime=runtime,
                        models=models,
                        model_profiles=model_profiles,
                        tools=tools,
                        commands=commands,
                        plugins=plugin_manager,
                        conversation_metadata=conversation_metadata,
                        scheduler=scheduler,
                        onebot_hub=onebot_hub,
                        onebot_sender=onebot_sender,
                        onebot_queries=onebot_queries,
                        onebot_management=onebot_management,
                        onebot_actions=onebot_actions,
                        events=events,
                    )
            finally:
                await plugin_manager.shutdown()
                await models.aclose()
