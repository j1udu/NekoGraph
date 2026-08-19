"""OneBot v11 reverse Universal WebSocket gateway."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

from websockets.asyncio.server import Server, ServerConnection, serve

from nekograph.application import MessageApplication
from nekograph.application.events import EventDisposition, EventRouter
from nekograph.logging import fields
from nekograph.models import (
    InboundMessageEvent,
    MessageSentEvent,
    OneBotEvent,
    UnknownOneBotEvent,
)
from nekograph.protocols.onebot_v11.actions import (
    OneBotActionError as OneBotActionError,
)
from nekograph.protocols.onebot_v11.actions import (
    OneBotActionFailedError,
    OneBotActionTimeoutError,
    OneBotConnectionHub,
    OneBotMessageSender,
)
from nekograph.protocols.onebot_v11.actions import (
    outbound_to_action as outbound_to_action,
)
from nekograph.protocols.onebot_v11.parser import (
    InvalidOneBotEventError,
    parse_onebot_event,
)

logger = logging.getLogger(__name__)


class _OneBotConnection:
    def __init__(self, socket: ServerConnection, timeout_seconds: float) -> None:
        self._socket = socket
        self._timeout_seconds = timeout_seconds
        self._pending: dict[str, asyncio.Future[dict[str, object]]] = {}
        self._send_lock = asyncio.Lock()

    def resolve_action_response(self, payload: dict[str, object]) -> bool:
        echo = payload.get("echo")
        if not isinstance(echo, str):
            return False
        future = self._pending.get(echo)
        if future is None or future.done():
            return False
        future.set_result(payload)
        return True

    async def call_action(self, action: str, params: dict[str, object]) -> dict[str, object]:
        echo = uuid4().hex
        future: asyncio.Future[dict[str, object]] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[echo] = future
        request = {"action": action, "params": params, "echo": echo}
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps(request, ensure_ascii=False))
            response = await asyncio.wait_for(future, timeout=self._timeout_seconds)
        except TimeoutError as exc:
            raise OneBotActionTimeoutError(
                f"OneBot action timed out: {action}"
            ) from exc
        finally:
            self._pending.pop(echo, None)

        if response.get("status") != "ok" or response.get("retcode") != 0:
            wording = response.get("wording")
            raise OneBotActionFailedError(
                action,
                response.get("retcode"),
                wording if isinstance(wording, str) else None,
            )
        return response

    def fail_pending(self, error: Exception | None = None) -> None:
        failure = error or ConnectionError("OneBot WebSocket disconnected")
        for future in self._pending.values():
            if not future.done():
                future.set_exception(failure)
        self._pending.clear()

    async def close(self, reason: str) -> None:
        await self._socket.close(code=1012, reason=reason)


class ReverseWebSocketGateway:
    def __init__(
        self,
        *,
        application: MessageApplication,
        hub: OneBotConnectionHub,
        sender: OneBotMessageSender,
        events: EventRouter,
        host: str,
        port: int,
        path: str,
        access_token: str | None,
        action_timeout_seconds: float,
    ) -> None:
        self._application = application
        self._hub = hub
        self._sender = sender
        self._events = events
        self._host = host
        self._port = port
        self._path = path
        self._access_token = access_token
        self._action_timeout_seconds = action_timeout_seconds

    @asynccontextmanager
    async def run(self) -> AsyncGenerator[Server]:
        async with serve(self._handle_connection, self._host, self._port) as server:
            yield server

    async def _handle_connection(self, socket: ServerConnection) -> None:
        request = socket.request
        if request is None or urlsplit(request.path).path != self._path:
            await socket.close(code=1008, reason="invalid OneBot WebSocket path")
            return

        bot_id = request.headers.get("X-Self-ID")
        role = request.headers.get("X-Client-Role", "")
        if not bot_id or role.casefold() != "universal":
            await socket.close(code=1008, reason="OneBot Universal client required")
            return
        if self._access_token is not None:
            expected = f"Bearer {self._access_token}"
            actual = request.headers.get("Authorization", "")
            if not hmac.compare_digest(actual, expected):
                await socket.close(code=1008, reason="invalid access token")
                return

        connection = _OneBotConnection(socket, self._action_timeout_seconds)
        tasks: set[asyncio.Task[None]] = set()
        await self._hub.attach(bot_id, connection)
        logger.info("onebot_connected", extra=fields(bot_id=bot_id, role=role))
        try:
            async for raw_message in socket:
                payload = self._decode_payload(raw_message, bot_id)
                if payload is None:
                    continue
                if connection.resolve_action_response(payload):
                    continue
                event = self._parse_event(payload, bot_id)
                if event is None:
                    continue
                task = asyncio.create_task(self._process_event(event))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        finally:
            await self._hub.detach(bot_id, connection)
            connection.fail_pending()
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("onebot_disconnected", extra=fields(bot_id=bot_id))

    @staticmethod
    def _decode_payload(
        raw_message: str | bytes, bot_id: str
    ) -> dict[str, object] | None:
        try:
            text = (
                raw_message.decode("utf-8")
                if isinstance(raw_message, bytes)
                else raw_message
            )
            decoded = json.loads(text)
            if not isinstance(decoded, dict):
                raise ValueError("WebSocket payload must be a JSON object")
            return cast(dict[str, object], decoded)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            logger.warning("onebot_invalid_json", extra=fields(bot_id=bot_id))
            return None

    @staticmethod
    def _parse_event(payload: dict[str, object], bot_id: str) -> OneBotEvent | None:
        try:
            event = parse_onebot_event(payload)
        except InvalidOneBotEventError as exc:
            logger.warning(
                "onebot_event_invalid",
                extra=fields(bot_id=bot_id, error=str(exc)),
            )
            return None
        if event.bot_id != bot_id:
            logger.warning(
                "onebot_self_id_mismatch",
                extra=fields(header_bot_id=bot_id, event_bot_id=event.bot_id),
            )
            return None
        return event

    async def _process_event(self, event: OneBotEvent) -> None:
        try:
            disposition = await self._events.dispatch(event)
            if not isinstance(event, InboundMessageEvent) or isinstance(
                event, MessageSentEvent
            ):
                if isinstance(event, UnknownOneBotEvent):
                    logger.info(
                        "onebot_unknown_event",
                        extra=fields(
                            bot_id=event.bot_id,
                            post_type=event.post_type,
                            event_type=event.event_type,
                        ),
                    )
                return
            if disposition is EventDisposition.CONSUMED:
                return
            response = await self._application.handle(event)
            if response is None:
                return
            await self._sender.send(
                response,
                source="message_reply",
                correlation_id=event.message.message_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "onebot_event_processing_failed",
                extra=fields(
                    bot_id=event.bot_id,
                    message_id=(
                        event.message.message_id
                        if isinstance(event, InboundMessageEvent)
                        else None
                    ),
                ),
            )
