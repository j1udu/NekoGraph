"""Persistent credentials/configuration for retrieval models."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class KnowledgeModelConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    kind: Literal["embedding", "reranker"]
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr = Field(min_length=1, max_length=1_000)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        parsed = httpx.URL(value)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("base_url must be an HTTP(S) URL")
        return value.rstrip("/")


class KnowledgeModelConfigStore:
    def __init__(self, path: Path, configs: dict[str, KnowledgeModelConfig]) -> None:
        self.path = path
        self._configs = configs

    @classmethod
    async def open(cls, path: Path) -> KnowledgeModelConfigStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        if await asyncio.to_thread(path.is_file):
            raw = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            configs = {
                item["kind"]: KnowledgeModelConfig.model_validate(item)
                for item in raw.get("models", [])
            }
        else:
            configs = {}
        return cls(path, configs)

    def get(self, kind: str) -> KnowledgeModelConfig | None:
        return self._configs.get(kind)

    def views(self) -> dict[str, dict[str, str | bool | None]]:
        return {
            kind: {
                "configured": config is not None,
                "base_url": config.base_url if config else None,
                "model": config.model if config else None,
            }
            for kind, config in (
                ("embedding", self.get("embedding")),
                ("reranker", self.get("reranker")),
            )
        }

    async def save(self, config: KnowledgeModelConfig) -> None:
        self._configs[config.kind] = config
        payload = {
            "models": [
                {
                    "kind": item.kind,
                    "base_url": item.base_url,
                    "model": item.model,
                    "api_key": item.api_key.get_secret_value(),
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in self._configs.values()
            ]
        }
        await asyncio.to_thread(
            self.path.write_text, json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
        )
        os.chmod(self.path, 0o600)

    async def delete(self, kind: str) -> None:
        self._configs.pop(kind, None)
        payload = {
            "models": [
                {
                    "kind": item.kind,
                    "base_url": item.base_url,
                    "model": item.model,
                    "api_key": item.api_key.get_secret_value(),
                    "timeout_seconds": item.timeout_seconds,
                }
                for item in self._configs.values()
            ]
        }
        await asyncio.to_thread(
            self.path.write_text, json.dumps(payload, ensure_ascii=False, indent=2), "utf-8"
        )
        os.chmod(self.path, 0o600)
