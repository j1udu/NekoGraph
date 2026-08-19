"""Typed runtime configuration loaded from environment variables."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nekograph.models import GroupConversationMode


class ModelBackend(StrEnum):
    FAKE = "fake"
    OPENAI_COMPATIBLE = "openai_compatible"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="NEKOGRAPH_",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=0, le=65535)
    websocket_path: str = "/onebot/v11/ws"
    access_token: str | None = None
    action_timeout_seconds: float = Field(default=10.0, gt=0)
    checkpoint_path: Path = Path("data/checkpoints.sqlite")
    conversation_metadata_path: Path = Path("data/conversations.sqlite")
    group_conversation_mode: GroupConversationMode = GroupConversationMode.PER_USER
    group_wake_prefixes: tuple[str, ...] = ("neko",)
    model_backend: ModelBackend = ModelBackend.FAKE
    llm_model: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: SecretStr | None = None
    llm_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    model_profiles_path: Path = Path("data/model-profiles.sqlite")
    dashboard_host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    dashboard_port: int = Field(default=5190, ge=0, le=65535)
    tool_sandbox_path: Path = Path("data/tool-sandbox")
    tool_execution_ledger_path: Path = Path("data/tool-executions.sqlite")
    tool_permissions: tuple[str, ...] = ("demo_file:write",)
    allow_dangerous_tools: bool = False
    tool_approval_ttl_seconds: float = Field(default=900.0, gt=0)

    @model_validator(mode="after")
    def validate_model_configuration(self) -> Settings:
        if self.model_backend is ModelBackend.OPENAI_COMPATIBLE:
            if not self.llm_model:
                raise ValueError("NEKOGRAPH_LLM_MODEL is required for openai_compatible")
            if self.llm_api_key is None or not self.llm_api_key.get_secret_value():
                raise ValueError("NEKOGRAPH_LLM_API_KEY is required for openai_compatible")
        return self
