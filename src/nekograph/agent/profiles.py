"""Persistent model profiles kept separate from agent checkpoints."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite
import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


class ModelProfileError(RuntimeError):
    """Base error exposed by the model profile store."""


class ModelProfileNotFoundError(ModelProfileError):
    """The requested profile does not exist."""


class DuplicateModelProfileError(ModelProfileError):
    """A profile with the same display name already exists."""


class ActiveModelProfileError(ModelProfileError):
    """An operation cannot remove the currently active profile."""


class ModelProfileConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    base_url: str = Field(min_length=1, max_length=500)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    timeout_seconds: float = Field(default=30.0, gt=0, le=600.0)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        try:
            parsed = httpx.URL(value)
        except httpx.InvalidURL as exc:
            raise ValueError("base_url must be a valid HTTP(S) URL") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            raise ValueError("base_url must be an HTTP(S) URL")
        return value


class ModelProfileInput(ModelProfileConfig):
    api_key: SecretStr = Field(min_length=1)


class ModelProfileUpdate(ModelProfileConfig):
    api_key: SecretStr | None = Field(default=None, min_length=1)


class StoredModelProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    name: str
    model: str
    base_url: str
    api_key: SecretStr
    temperature: float
    timeout_seconds: float
    created_at: datetime
    active: bool = False


class ModelProfileView(BaseModel):
    model_config = ConfigDict(frozen=True)

    profile_id: str
    name: str
    model: str
    base_url: str
    temperature: float
    timeout_seconds: float
    created_at: datetime
    active: bool
    has_api_key: bool = True


def profile_view(profile: StoredModelProfile) -> ModelProfileView:
    return ModelProfileView(
        profile_id=profile.profile_id,
        name=profile.name,
        model=profile.model,
        base_url=profile.base_url,
        temperature=profile.temperature,
        timeout_seconds=profile.timeout_seconds,
        created_at=profile.created_at,
        active=profile.active,
    )


class ModelProfileStore:
    def __init__(self, connection: aiosqlite.Connection, path: Path) -> None:
        self._connection = connection
        self._path = path

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path) -> AsyncGenerator[ModelProfileStore]:
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(path) as connection:
            connection.row_factory = aiosqlite.Row
            store = cls(connection, path)
            await store.setup()
            yield store

    async def setup(self) -> None:
        await self._connection.execute("PRAGMA foreign_keys = ON")
        await self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS model_profiles (
                profile_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                model TEXT NOT NULL,
                base_url TEXT NOT NULL,
                api_key TEXT NOT NULL,
                temperature REAL NOT NULL,
                timeout_seconds REAL NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS model_profile_settings (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                active_profile_id TEXT NULL,
                FOREIGN KEY (active_profile_id) REFERENCES model_profiles(profile_id)
            );

            INSERT OR IGNORE INTO model_profile_settings(singleton, active_profile_id)
            VALUES (1, NULL);
            """
        )
        await self._connection.commit()
        os.chmod(self._path, 0o600)

    async def list(self) -> tuple[StoredModelProfile, ...]:
        cursor = await self._connection.execute(
            """
            SELECT p.*, s.active_profile_id = p.profile_id AS active
            FROM model_profiles AS p
            CROSS JOIN model_profile_settings AS s
            WHERE s.singleton = 1
            ORDER BY p.name COLLATE NOCASE, p.profile_id
            """
        )
        rows = await cursor.fetchall()
        return tuple(self._from_row(row) for row in rows)

    async def get(self, profile_id: str) -> StoredModelProfile:
        cursor = await self._connection.execute(
            """
            SELECT p.*, s.active_profile_id = p.profile_id AS active
            FROM model_profiles AS p
            CROSS JOIN model_profile_settings AS s
            WHERE s.singleton = 1 AND p.profile_id = ?
            """,
            (profile_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ModelProfileNotFoundError("model profile was not found")
        return self._from_row(row)

    async def active(self) -> StoredModelProfile | None:
        cursor = await self._connection.execute(
            """
            SELECT p.*, 1 AS active
            FROM model_profiles AS p
            JOIN model_profile_settings AS s ON s.active_profile_id = p.profile_id
            WHERE s.singleton = 1
            """
        )
        row = await cursor.fetchone()
        return self._from_row(row) if row is not None else None

    async def create_many(
        self, profiles: Sequence[ModelProfileInput]
    ) -> tuple[StoredModelProfile, ...]:
        if not profiles:
            return ()
        now = datetime.now(UTC)
        values = [
            (
                uuid4().hex,
                profile.name,
                profile.model,
                profile.base_url,
                profile.api_key.get_secret_value(),
                profile.temperature,
                profile.timeout_seconds,
                now.isoformat(),
            )
            for profile in profiles
        ]
        try:
            await self._connection.executemany(
                """
                INSERT INTO model_profiles(
                    profile_id, name, model, base_url, api_key,
                    temperature, timeout_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            await self._connection.commit()
        except aiosqlite.IntegrityError as exc:
            await self._connection.rollback()
            raise DuplicateModelProfileError(
                "model profile names must be unique"
            ) from exc
        created: list[StoredModelProfile] = []
        for value in values:
            created.append(await self.get(str(value[0])))
        return tuple(created)

    async def set_active(self, profile_id: str | None) -> None:
        if profile_id is not None:
            await self.get(profile_id)
        await self._connection.execute(
            """
            UPDATE model_profile_settings
            SET active_profile_id = ?
            WHERE singleton = 1
            """,
            (profile_id,),
        )
        await self._connection.commit()

    async def update(
        self, profile_id: str, changes: ModelProfileUpdate
    ) -> StoredModelProfile:
        existing = await self.get(profile_id)
        api_key = (
            changes.api_key.get_secret_value()
            if changes.api_key is not None
            else existing.api_key.get_secret_value()
        )
        try:
            cursor = await self._connection.execute(
                """
                UPDATE model_profiles
                SET name = ?, model = ?, base_url = ?, api_key = ?,
                    temperature = ?, timeout_seconds = ?
                WHERE profile_id = ?
                """,
                (
                    changes.name,
                    changes.model,
                    changes.base_url,
                    api_key,
                    changes.temperature,
                    changes.timeout_seconds,
                    profile_id,
                ),
            )
            await self._connection.commit()
        except aiosqlite.IntegrityError as exc:
            await self._connection.rollback()
            raise DuplicateModelProfileError(
                "model profile names must be unique"
            ) from exc
        if cursor.rowcount == 0:
            raise ModelProfileNotFoundError("model profile was not found")
        return await self.get(profile_id)

    async def delete(self, profile_id: str) -> None:
        profile = await self.get(profile_id)
        if profile.active:
            raise ActiveModelProfileError(
                "activate another model before deleting this profile"
            )
        cursor = await self._connection.execute(
            "DELETE FROM model_profiles WHERE profile_id = ?",
            (profile_id,),
        )
        await self._connection.commit()
        if cursor.rowcount == 0:
            raise ModelProfileNotFoundError("model profile was not found")

    @staticmethod
    def _from_row(row: aiosqlite.Row) -> StoredModelProfile:
        return StoredModelProfile(
            profile_id=str(row["profile_id"]),
            name=str(row["name"]),
            model=str(row["model"]),
            base_url=str(row["base_url"]),
            api_key=SecretStr(str(row["api_key"])),
            temperature=float(row["temperature"]),
            timeout_seconds=float(row["timeout_seconds"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
            active=bool(row["active"]),
        )
