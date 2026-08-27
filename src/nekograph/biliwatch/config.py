"""Locally persisted BiliWatch settings with redacted public views."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class BiliWatchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    admins: tuple[str, ...] = ()
    poll_interval_seconds: int = Field(default=30, ge=20, le=3600)
    sessdata: SecretStr | None = None
    bili_jct: SecretStr | None = None
    dede_user_id: SecretStr | None = None

    @field_validator("admins")
    @classmethod
    def validate_admins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(item.strip() for item in value if item.strip()))
        if any(not item.isdigit() for item in normalized):
            raise ValueError("administrator QQ IDs must be numeric")
        return normalized

    @property
    def cookie_header(self) -> str:
        values = (
            ("SESSDATA", self.sessdata),
            ("bili_jct", self.bili_jct),
            ("DedeUserID", self.dede_user_id),
        )
        return "; ".join(
            f"{name}={value.get_secret_value()}"
            for name, value in values
            if value is not None and value.get_secret_value()
        )


class BiliWatchConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    admins: tuple[str, ...]
    poll_interval_seconds: int = Field(ge=20, le=3600)
    sessdata: str | None = Field(default=None, max_length=4096)
    bili_jct: str | None = Field(default=None, max_length=4096)
    dede_user_id: str | None = Field(default=None, max_length=4096)


class BiliWatchConfigStore:
    def __init__(self, path: Path, config: BiliWatchConfig) -> None:
        self.path = path
        self._config = config
        self._lock = asyncio.Lock()

    @classmethod
    async def open(
        cls, path: Path, defaults: BiliWatchConfig | None = None
    ) -> BiliWatchConfigStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        if await asyncio.to_thread(path.is_file):
            raw = json.loads(await asyncio.to_thread(path.read_text, encoding="utf-8"))
            config = BiliWatchConfig.model_validate(raw)
        else:
            config = defaults or BiliWatchConfig()
        return cls(path, config)

    @property
    def current(self) -> BiliWatchConfig:
        return self._config

    def view(self) -> dict[str, object]:
        config = self._config
        return {
            "admins": list(config.admins),
            "poll_interval_seconds": config.poll_interval_seconds,
            "sessdata_configured": bool(config.sessdata and config.sessdata.get_secret_value()),
            "bili_jct_configured": bool(
                config.bili_jct and config.bili_jct.get_secret_value()
            ),
            "dede_user_id_configured": bool(
                config.dede_user_id and config.dede_user_id.get_secret_value()
            ),
        }

    async def update(self, update: BiliWatchConfigUpdate) -> BiliWatchConfig:
        async with self._lock:
            current = self._config
            self._config = BiliWatchConfig(
                admins=update.admins,
                poll_interval_seconds=update.poll_interval_seconds,
                sessdata=_updated_secret(update.sessdata, current.sessdata),
                bili_jct=_updated_secret(update.bili_jct, current.bili_jct),
                dede_user_id=_updated_secret(update.dede_user_id, current.dede_user_id),
            )
            await self._persist()
            return self._config

    async def _persist(self) -> None:
        config = self._config
        payload = {
            "admins": list(config.admins),
            "poll_interval_seconds": config.poll_interval_seconds,
            "sessdata": _secret_value(config.sessdata),
            "bili_jct": _secret_value(config.bili_jct),
            "dede_user_id": _secret_value(config.dede_user_id),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        await asyncio.to_thread(self.path.write_text, text, "utf-8")
        os.chmod(self.path, 0o600)


def _secret_value(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _updated_secret(value: str | None, current: SecretStr | None) -> SecretStr | None:
    if value is None or not value.strip():
        return current
    return SecretStr(value.strip())
