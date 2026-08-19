"""OpenAI-compatible Chat Completions adapter."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nekograph.model_types import ModelToolSpec


class ModelProviderError(RuntimeError):
    """Base error exposed by a model provider adapter."""


class ModelProviderTimeoutError(ModelProviderError):
    """The provider didn't respond before the configured timeout."""


class ModelProviderAuthenticationError(ModelProviderError):
    """The provider rejected its API credentials."""


class ModelProviderTransportError(ModelProviderError):
    """The provider couldn't be reached."""


class ModelProviderResponseError(ModelProviderError):
    """The provider returned an unsuccessful or malformed response."""


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    model: str
    base_url: str
    api_key: str = field(repr=False)
    temperature: float = 0.0
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        try:
            parsed_url = httpx.URL(self.base_url)
        except httpx.InvalidURL as exc:
            raise ValueError("base_url must be a valid HTTP(S) URL") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.host:
            raise ValueError("base_url must be an HTTP(S) URL")
        if not self.api_key:
            raise ValueError("api_key must not be empty")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be between 0 and 2")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")


class _FunctionCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    arguments: str


class _ProviderToolCall(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    type: Literal["function"]
    function: _FunctionCall


class _ProviderMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    role: Literal["assistant"]
    content: str | None = None
    tool_calls: list[_ProviderToolCall] = Field(default_factory=lambda: [])


class _Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    message: _ProviderMessage
    finish_reason: str | None = None


class _ChatCompletion(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    model: str | None = None
    choices: list[_Choice]


def _message_content(message: AnyMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    raise ModelProviderResponseError("only text message content is supported")


def _encode_message(message: AnyMessage) -> dict[str, Any]:
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": _message_content(message)}
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": _message_content(message)}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": _message_content(message),
            "tool_call_id": message.tool_call_id,
        }
    if isinstance(message, AIMessage):
        encoded: dict[str, Any] = {
            "role": "assistant",
            "content": _message_content(message),
        }
        if message.tool_calls:
            encoded["tool_calls"] = [
                {
                    "id": tool_call["id"],
                    "type": "function",
                    "function": {
                        "name": tool_call["name"],
                        "arguments": json.dumps(
                            tool_call["args"], ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                }
                for tool_call in message.tool_calls
            ]
        return encoded
    raise ModelProviderResponseError(f"unsupported message type: {message.type}")


class OpenAICompatibleChatModel:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(config.timeout_seconds),
            transport=transport,
        )

    async def __aenter__(self) -> OpenAICompatibleChatModel:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[ModelToolSpec] = (),
    ) -> AIMessage:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [_encode_message(message) for message in messages],
            "temperature": self._config.temperature,
        }
        if tools:
            payload["tools"] = list(tools)

        endpoint = f"{self._config.base_url.rstrip('/')}/chat/completions"
        try:
            response = await self._client.post(endpoint, json=payload)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelProviderTimeoutError("model provider request timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ModelProviderAuthenticationError(
                    "model provider rejected authentication"
                ) from exc
            raise ModelProviderResponseError(
                f"model provider returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelProviderTransportError("model provider request failed") from exc

        try:
            completion = _ChatCompletion.model_validate(response.json())
            choice = completion.choices[0]
            tool_calls = [
                {
                    "id": item.id,
                    "name": item.function.name,
                    "args": json.loads(item.function.arguments),
                    "type": "tool_call",
                }
                for item in choice.message.tool_calls
            ]
        except (IndexError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ModelProviderResponseError("model provider returned an invalid response") from exc

        if any(not isinstance(tool_call["args"], dict) for tool_call in tool_calls):
            raise ModelProviderResponseError("model provider returned invalid tool arguments")
        return AIMessage(
            content=choice.message.content or "",
            id=completion.id,
            tool_calls=tool_calls,
            response_metadata={
                "model": completion.model,
                "finish_reason": choice.finish_reason,
            },
        )

    async def test_connection(self) -> None:
        endpoint = f"{self._config.base_url.rstrip('/')}/models"
        try:
            response = await self._client.get(endpoint)
            response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise ModelProviderTimeoutError("model provider request timed out") from exc
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code in {401, 403}:
                raise ModelProviderAuthenticationError(
                    "model provider rejected authentication"
                ) from exc
            raise ModelProviderResponseError(
                f"model provider returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise ModelProviderTransportError("model provider request failed") from exc
