from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from nekograph.config import ModelBackend, Settings
from nekograph.logging import JsonFormatter, fields
from nekograph.models import GroupConversationMode


def test_settings_defaults_are_safe_for_local_development() -> None:
    settings = Settings()

    assert settings.host == "127.0.0.1"
    assert settings.websocket_path == "/onebot/v11/ws"
    assert settings.group_conversation_mode is GroupConversationMode.PER_USER
    assert settings.checkpoint_path == Path("data/checkpoints.sqlite")
    assert settings.model_backend is ModelBackend.FAKE


def test_settings_can_switch_group_mode_and_prefixes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKOGRAPH_GROUP_CONVERSATION_MODE", "shared")
    monkeypatch.setenv("NEKOGRAPH_GROUP_WAKE_PREFIXES", '["猫猫", "neko"]')

    settings = Settings()

    assert settings.group_conversation_mode is GroupConversationMode.SHARED
    assert settings.group_wake_prefixes == ("猫猫", "neko")


def test_openai_compatible_settings_require_model_and_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NEKOGRAPH_MODEL_BACKEND", "openai_compatible")

    with pytest.raises(ValidationError, match="NEKOGRAPH_LLM_MODEL"):
        Settings()


def test_openai_compatible_settings_hide_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NEKOGRAPH_MODEL_BACKEND", "openai_compatible")
    monkeypatch.setenv("NEKOGRAPH_LLM_MODEL", "test-model")
    monkeypatch.setenv("NEKOGRAPH_LLM_API_KEY", "test-secret")

    settings = Settings()

    assert settings.model_backend is ModelBackend.OPENAI_COMPATIBLE
    assert settings.llm_api_key is not None
    assert settings.llm_api_key.get_secret_value() == "test-secret"
    assert "test-secret" not in repr(settings)


def test_json_formatter_emits_machine_readable_context() -> None:
    record = logging.LogRecord(
        name="nekograph.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="message_processed",
        args=(),
        exc_info=None,
    )
    for key, value in fields(run_id="run-1", conversation_id="conversation-1").items():
        setattr(record, key, value)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "message_processed"
    assert payload["run_id"] == "run-1"
    assert payload["conversation_id"] == "conversation-1"
