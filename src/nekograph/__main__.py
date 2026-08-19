"""NekoGraph process entry point."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from nekograph.agent import (
    ChatModel,
    FakeChatModel,
    LangGraphRuntime,
    OpenAICompatibleChatModel,
    OpenAICompatibleConfig,
)
from nekograph.application.conversation import ConversationResolver
from nekograph.application.scheduler import ConversationScheduler
from nekograph.application.service import MessageApplication
from nekograph.application.wakeup import WakeupPolicy
from nekograph.config import ModelBackend, Settings
from nekograph.logging import configure_logging, fields
from nekograph.protocols.onebot_v11.gateway import ReverseWebSocketGateway
from nekograph.tools import ToolExecutionContext, build_core_tool_registry

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_configured_model(settings: Settings) -> AsyncGenerator[ChatModel]:
    if settings.model_backend is ModelBackend.FAKE:
        logger.warning("fake_model_active")
        yield FakeChatModel()
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
        yield model


async def run(settings: Settings) -> None:
    tools = build_core_tool_registry(settings.tool_sandbox_path)
    tool_context = ToolExecutionContext(
        permissions=frozenset(settings.tool_permissions),
        allow_dangerous=settings.allow_dangerous_tools,
    )
    async with (
        open_configured_model(settings) as model,
        LangGraphRuntime.open(
            checkpoint_path=settings.checkpoint_path,
            model=model,
            tools=tools,
            tool_context=tool_context,
            execution_ledger_path=settings.tool_execution_ledger_path,
            approval_ttl_seconds=settings.tool_approval_ttl_seconds,
        ) as runtime,
    ):
        application = MessageApplication(
            runtime=runtime,
            conversations=ConversationResolver(settings.group_conversation_mode),
            wakeup=WakeupPolicy(settings.group_wake_prefixes),
            scheduler=ConversationScheduler(),
        )
        gateway = ReverseWebSocketGateway(
            application=application,
            host=settings.host,
            port=settings.port,
            path=settings.websocket_path,
            access_token=settings.access_token,
            action_timeout_seconds=settings.action_timeout_seconds,
        )
        async with gateway.run() as server:
            addresses = [str(socket.getsockname()) for socket in server.sockets]
            logger.info(
                "nekograph_started",
                extra=fields(
                    addresses=addresses,
                    path=settings.websocket_path,
                    model_backend=settings.model_backend,
                ),
            )
            await server.serve_forever()


def main() -> None:
    configure_logging()
    try:
        asyncio.run(run(Settings()))
    except KeyboardInterrupt:
        logger.info("nekograph_stopped")


if __name__ == "__main__":
    main()
