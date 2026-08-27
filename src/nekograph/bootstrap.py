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
from nekograph.biliwatch import (
    BilibiliClient,
    BiliWatchConfig,
    BiliWatchConfigStore,
    BiliWatchService,
    BiliWatchStore,
)
from nekograph.biliwatch.commands import register_biliwatch_commands
from nekograph.config import ModelBackend, Settings
from nekograph.knowledge import (
    KnowledgeService,
    OpenAICompatibleEmbedding,
    OpenAICompatibleReranker,
)
from nekograph.knowledge.config_store import KnowledgeModelConfig, KnowledgeModelConfigStore
from nekograph.knowledge.tools import register_knowledge_tool
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
        yield (
            FakeChatModel(),
            ActiveModelInfo(
                profile_id=None,
                name="Environment fallback",
                model="fake",
                base_url=None,
                source="environment",
            ),
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
        yield (
            model,
            ActiveModelInfo(
                profile_id=None,
                name="Environment fallback",
                model=settings.llm_model,
                base_url=settings.llm_base_url,
                source="environment",
            ),
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
    knowledge: KnowledgeService
    knowledge_models: KnowledgeModelConfigStore
    biliwatch: BiliWatchService
    biliwatch_store: BiliWatchStore
    biliwatch_config: BiliWatchConfigStore

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
    async with _open_knowledge_model_store(settings) as knowledge_models:
        embedding = None
        reranker = None
        embedding_config = knowledge_models.get("embedding")
        if (
            embedding_config is None
            and settings.knowledge_embedding_base_url
            and settings.knowledge_embedding_model
            and settings.knowledge_embedding_api_key
        ):
            embedding_config = KnowledgeModelConfig(
                kind="embedding",
                base_url=settings.knowledge_embedding_base_url,
                model=settings.knowledge_embedding_model,
                api_key=settings.knowledge_embedding_api_key,
                timeout_seconds=settings.knowledge_embedding_timeout_seconds,
            )
        reranker_config = knowledge_models.get("reranker")
        if (
            reranker_config is None
            and settings.knowledge_rerank_base_url
            and settings.knowledge_rerank_model
            and settings.knowledge_rerank_api_key
        ):
            reranker_config = KnowledgeModelConfig(
                kind="reranker",
                base_url=settings.knowledge_rerank_base_url,
                model=settings.knowledge_rerank_model,
                api_key=settings.knowledge_rerank_api_key,
                timeout_seconds=settings.knowledge_rerank_timeout_seconds,
            )
        if embedding_config:
            embedding = OpenAICompatibleEmbedding(
                base_url=embedding_config.base_url,
                model=embedding_config.model,
                api_key=embedding_config.api_key.get_secret_value(),
                timeout_seconds=embedding_config.timeout_seconds,
            )
        if reranker_config:
            reranker = OpenAICompatibleReranker(
                base_url=reranker_config.base_url,
                model=reranker_config.model,
                api_key=reranker_config.api_key.get_secret_value(),
                timeout_seconds=reranker_config.timeout_seconds,
            )
        async with KnowledgeService.lifespan(
            settings.knowledge_path,
            embedding,
            settings.knowledge_index_path,
            reranker,
        ) as knowledge:
            await knowledge.ensure_collection("yousa", "泠鸢 yousa 专题资料")
            register_knowledge_tool(tools, knowledge)
            async with _open_runtime_resources(
                settings, plugins, tools, knowledge, knowledge_models
            ) as resources:
                yield resources


@asynccontextmanager
async def _open_knowledge_model_store(
    settings: Settings,
) -> AsyncGenerator[KnowledgeModelConfigStore]:
    yield await KnowledgeModelConfigStore.open(settings.knowledge_models_path)


@asynccontextmanager
async def _open_runtime_resources(
    settings: Settings,
    plugins: Iterable[Plugin],
    tools: ToolRegistry,
    knowledge: KnowledgeService,
    knowledge_models: KnowledgeModelConfigStore,
) -> AsyncGenerator[RuntimeResources]:
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
    biliwatch_config = await BiliWatchConfigStore.open(
        settings.biliwatch_config_path,
        BiliWatchConfig(
            admins=settings.biliwatch_admins,
            poll_interval_seconds=settings.biliwatch_poll_interval_seconds,
            sessdata=settings.biliwatch_sessdata,
            bili_jct=settings.biliwatch_bili_jct,
            dede_user_id=settings.biliwatch_dede_user_id,
        ),
    )

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
        BiliWatchStore.open(settings.biliwatch_path) as biliwatch_store,
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
        biliwatch_client = BilibiliClient(biliwatch_config)
        biliwatch = BiliWatchService(
            biliwatch_store,
            biliwatch_client,
            onebot_sender,
            biliwatch_config,
        )

        async def onebot_send_handler(context: TaskHandlerContext) -> None:
            payload = ScheduledOneBotMessage.model_validate(dict(context.payload))
            await onebot_sender.send(
                payload.outbound(),
                source="scheduled_task",
                correlation_id=context.run_id,
            )

        task_handlers.register("core.onebot_send", onebot_send_handler)

        async def biliwatch_poll_handler(context: TaskHandlerContext) -> None:
            report = await biliwatch.poll()
            logger.info(
                "biliwatch_poll_completed",
                extra={"run_id": context.run_id, **report.model_dump()},
            )

        task_handlers.register("biliwatch.poll", biliwatch_poll_handler)
        async with SchedulerRuntime.open(
            settings.scheduled_tasks_path,
            task_handlers,
            max_concurrency=settings.scheduled_task_max_concurrency,
        ) as scheduler:
            await biliwatch.bind_scheduler(scheduler)
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
                    register_biliwatch_commands(commands, biliwatch, biliwatch_config)
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
                        knowledge=knowledge,
                        knowledge_models=knowledge_models,
                        biliwatch=biliwatch,
                        biliwatch_store=biliwatch_store,
                        biliwatch_config=biliwatch_config,
                    )
            finally:
                await biliwatch_client.aclose()
                await plugin_manager.shutdown()
                await models.aclose()
