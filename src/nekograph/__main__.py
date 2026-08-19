"""NekoGraph process entry point."""

from __future__ import annotations

import asyncio
import logging
from argparse import ArgumentParser, Namespace
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

import uvicorn

from nekograph.agent import ChatModel
from nekograph.bootstrap import open_configured_model as open_model_with_info
from nekograph.bootstrap import open_runtime_resources
from nekograph.config import Settings
from nekograph.logging import configure_logging, fields
from nekograph.protocols.local_console import LocalConsoleAdapter
from nekograph.protocols.onebot_v11.gateway import ReverseWebSocketGateway
from nekograph.web import create_dashboard_app

logger = logging.getLogger(__name__)


@asynccontextmanager
async def open_configured_model(settings: Settings) -> AsyncGenerator[ChatModel]:
    async with open_model_with_info(settings) as configured:
        model, _ = configured
        yield model


async def run(settings: Settings) -> None:
    async with open_runtime_resources(settings) as resources:
        application = resources.application()
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


async def run_chat(settings: Settings) -> None:
    async with open_runtime_resources(settings) as resources:
        application = resources.application(conversation_namespace="local:v1")
        logger.info(
            "local_chat_started",
            extra=fields(model_backend=settings.model_backend),
        )
        await LocalConsoleAdapter(application).run()


async def run_dashboard(settings: Settings) -> None:
    server = uvicorn.Server(
        uvicorn.Config(
            create_dashboard_app(settings),
            host=settings.dashboard_host,
            port=settings.dashboard_port,
            log_config=None,
        )
    )
    await server.serve()


def parse_args(argv: Sequence[str] | None = None) -> Namespace:
    parser = ArgumentParser(prog="nekograph")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=("gateway", "chat", "dashboard"),
        default="gateway",
        help="run the OneBot gateway (default), local chat, or management dashboard",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    configure_logging()
    args = parse_args(argv)
    try:
        settings = Settings()
        if args.mode == "chat":
            asyncio.run(run_chat(settings))
        elif args.mode == "dashboard":
            asyncio.run(run_dashboard(settings))
        else:
            asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("nekograph_stopped")


if __name__ == "__main__":
    main()
