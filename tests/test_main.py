from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from nekograph.__main__ import open_configured_model, parse_args
from nekograph.agent import FakeChatModel, OpenAICompatibleChatModel
from nekograph.config import ModelBackend, Settings


def test_cli_defaults_to_gateway_and_supports_local_chat() -> None:
    assert parse_args([]).mode == "gateway"
    assert parse_args(["gateway"]).mode == "gateway"
    assert parse_args(["chat"]).mode == "chat"
    assert parse_args(["dashboard"]).mode == "dashboard"
    assert parse_args(["serve"]).mode == "serve"


async def test_model_lifecycle_selects_fake_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings()

    async with open_configured_model(settings) as model:
        assert isinstance(model, FakeChatModel)


async def test_model_lifecycle_selects_openai_compatible_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    settings = Settings(
        model_backend=ModelBackend.OPENAI_COMPATIBLE,
        llm_model="test-model",
        llm_base_url="https://provider.example/v1",
        llm_api_key=SecretStr("test-secret"),
    )

    async with open_configured_model(settings) as model:
        assert isinstance(model, OpenAICompatibleChatModel)
