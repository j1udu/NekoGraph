from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, cast

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from nekograph.agent.model import FakeChatModel
from nekograph.agent.openai_compatible import (
    ModelProviderAuthenticationError,
    ModelProviderResponseError,
    ModelProviderTimeoutError,
    ModelProviderTransportError,
    OpenAICompatibleChatModel,
    OpenAICompatibleConfig,
)
from nekograph.model_types import ModelToolSpec


def tool_spec() -> ModelToolSpec:
    return {
        "type": "function",
        "function": {
            "name": "get_current_time",
            "description": "Return the current time.",
            "parameters": {
                "type": "object",
                "properties": {"timezone": {"type": "string"}},
                "required": ["timezone"],
            },
        },
    }


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"model": ""}, "model"),
        ({"base_url": "http://"}, "base_url"),
        ({"base_url": "ftp://provider.example"}, "base_url"),
        ({"api_key": ""}, "api_key"),
        ({"temperature": -0.1}, "temperature"),
        ({"temperature": 2.1}, "temperature"),
        ({"timeout_seconds": 0}, "timeout"),
    ],
)
def test_openai_compatible_config_rejects_invalid_values(
    overrides: dict[str, object], message: str
) -> None:
    values: dict[str, object] = {
        "model": "test-model",
        "base_url": "https://provider.example/v1",
        "api_key": "test-secret",
        "temperature": 0.0,
        "timeout_seconds": 5.0,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=message):
        OpenAICompatibleConfig(**values)  # type: ignore[arg-type]


async def test_fake_model_can_return_scripted_tool_calls() -> None:
    scripted = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": "get_current_time",
                "args": {"timezone": "UTC"},
                "type": "tool_call",
            }
        ],
    )
    model = FakeChatModel(scripted_responses=[scripted])

    response = await model.complete([HumanMessage(content="time?")], [tool_spec()])

    assert response == scripted
    assert response.tool_calls[0]["id"] == "call-1"
    assert model.calls == 1
    assert model.received_tools == [(tool_spec(),)]


async def test_openai_compatible_adapter_maps_messages_tools_and_response() -> None:
    captured: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["Authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "completion-1",
                "model": "test-model",
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_current_time",
                                        "arguments": '{"timezone":"UTC"}',
                                    },
                                }
                            ],
                        },
                    }
                ],
            },
        )

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
        temperature=0.2,
        timeout_seconds=5,
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        response = await model.complete(
            [
                HumanMessage(content="time?"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "id": "previous-call",
                            "name": "get_current_time",
                            "args": {"timezone": "UTC"},
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(content="12:00", tool_call_id="previous-call"),
            ],
            [tool_spec()],
        )

    assert captured["authorization"] == "Bearer test-secret"
    payload = cast(dict[str, Any], captured["payload"])
    assert payload["model"] == "test-model"
    assert payload["temperature"] == 0.2
    assert payload["tools"] == [tool_spec()]
    messages = cast(Sequence[dict[str, Any]], payload["messages"])
    assert messages[-1] == {
        "role": "tool",
        "content": "12:00",
        "tool_call_id": "previous-call",
    }
    assert response.content == ""
    assert response.tool_calls == [
        {
            "id": "call-1",
            "name": "get_current_time",
            "args": {"timezone": "UTC"},
            "type": "tool_call",
        }
    ]
    assert response.response_metadata == {
        "model": "test-model",
        "finish_reason": "tool_calls",
    }


async def test_openai_compatible_adapter_maps_text_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "hello"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        response = await model.complete([HumanMessage(content="hi")])

    assert response.content == "hello"
    assert response.tool_calls == []


async def test_openai_compatible_adapter_tests_connection() -> None:
    captured: dict[str, str] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        return httpx.Response(200, json={"object": "list", "data": []})

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        await model.test_connection()

    assert captured == {
        "method": "GET",
        "url": "https://provider.example/v1/models",
        "authorization": "Bearer test-secret",
    }


@pytest.mark.parametrize("status_code", [401, 403, 500])
async def test_openai_compatible_connection_maps_http_errors(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        expected = (
            ModelProviderAuthenticationError
            if status_code in {401, 403}
            else ModelProviderResponseError
        )
        with pytest.raises(expected):
            await model.test_connection()


@pytest.mark.parametrize("status_code", [401, 403])
async def test_openai_compatible_adapter_maps_authentication_errors(status_code: int) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "do not expose me"}})

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        with pytest.raises(ModelProviderAuthenticationError, match="authentication") as caught:
            await model.complete([HumanMessage(content="hi")])

    assert "do not expose me" not in str(caught.value)
    assert "test-secret" not in repr(config)


async def test_openai_compatible_adapter_maps_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider detail", request=request)

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        with pytest.raises(ModelProviderTimeoutError, match="timed out"):
            await model.complete([HumanMessage(content="hi")])


async def test_openai_compatible_adapter_maps_transport_error() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private network detail", request=request)

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        with pytest.raises(ModelProviderTransportError, match="request failed") as caught:
            await model.complete([HumanMessage(content="hi")])

    assert "private network detail" not in str(caught.value)


async def test_openai_compatible_adapter_rejects_malformed_tool_arguments() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {
                                        "name": "tool",
                                        "arguments": "not-json",
                                    },
                                }
                            ],
                        }
                    }
                ]
            },
        )

    config = OpenAICompatibleConfig(
        model="test-model",
        base_url="https://provider.example/v1",
        api_key="test-secret",
    )
    async with OpenAICompatibleChatModel(
        config, transport=httpx.MockTransport(handler)
    ) as model:
        with pytest.raises(ModelProviderResponseError, match="invalid response"):
            await model.complete([HumanMessage(content="hi")])
