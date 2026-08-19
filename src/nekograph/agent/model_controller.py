"""Concurrency-safe selection among persisted model profiles."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage

from nekograph.agent.model import ChatModel
from nekograph.agent.openai_compatible import (
    OpenAICompatibleChatModel,
    OpenAICompatibleConfig,
)
from nekograph.agent.profiles import ModelProfileStore, StoredModelProfile
from nekograph.model_types import ModelToolSpec

CloseModel = Callable[[], Awaitable[None]]


async def _no_op_close() -> None:
    return None


@dataclass(frozen=True, slots=True)
class ModelHandle:
    model: ChatModel
    close: CloseModel = _no_op_close


@dataclass(frozen=True, slots=True)
class ActiveModelInfo:
    profile_id: str | None
    name: str
    model: str
    base_url: str | None
    source: str


type ProfileModelFactory = Callable[[StoredModelProfile], ModelHandle]


def openai_profile_model(profile: StoredModelProfile) -> ModelHandle:
    model = OpenAICompatibleChatModel(
        OpenAICompatibleConfig(
            model=profile.model,
            base_url=profile.base_url,
            api_key=profile.api_key.get_secret_value(),
            temperature=profile.temperature,
            timeout_seconds=profile.timeout_seconds,
        )
    )
    return ModelHandle(model=model, close=model.aclose)


class ModelController:
    def __init__(
        self,
        *,
        store: ModelProfileStore,
        fallback: ModelHandle,
        fallback_info: ActiveModelInfo,
        profile_factory: ProfileModelFactory = openai_profile_model,
    ) -> None:
        self._store = store
        self._fallback = fallback
        self._fallback_info = fallback_info
        self._profile_factory = profile_factory
        self._active = fallback
        self._active_info = fallback_info
        self._active_is_profile = False
        self._lock = asyncio.Lock()

    @property
    def active_info(self) -> ActiveModelInfo:
        return self._active_info

    async def initialize(self) -> None:
        profile = await self._store.active()
        if profile is not None:
            await self._switch_to_profile(profile, persist=False)

    async def complete(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[ModelToolSpec] = (),
    ) -> AIMessage:
        async with self._lock:
            return await self._active.model.complete(messages, tools)

    async def activate(self, profile_id: str | None) -> ActiveModelInfo:
        if profile_id is None:
            await self._store.set_active(None)
            async with self._lock:
                old = self._active if self._active_is_profile else None
                self._active = self._fallback
                self._active_info = self._fallback_info
                self._active_is_profile = False
                if old is not None:
                    await old.close()
            return self._active_info

        profile = await self._store.get(profile_id)
        await self._switch_to_profile(profile, persist=True)
        return self._active_info

    async def aclose(self) -> None:
        async with self._lock:
            if self._active_is_profile:
                await self._active.close()
                self._active = self._fallback
                self._active_info = self._fallback_info
                self._active_is_profile = False

    async def _switch_to_profile(
        self, profile: StoredModelProfile, *, persist: bool
    ) -> None:
        replacement = self._profile_factory(profile)
        try:
            if persist:
                await self._store.set_active(profile.profile_id)
            async with self._lock:
                old = self._active if self._active_is_profile else None
                self._active = replacement
                self._active_info = ActiveModelInfo(
                    profile_id=profile.profile_id,
                    name=profile.name,
                    model=profile.model,
                    base_url=profile.base_url,
                    source="profile",
                )
                self._active_is_profile = True
                if old is not None:
                    await old.close()
        except Exception:
            await replacement.close()
            raise
