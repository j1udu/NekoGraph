"""Serialize work per conversation while preserving cross-conversation concurrency."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class _LockEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0


class ConversationScheduler:
    def __init__(self) -> None:
        self._entries: dict[str, _LockEntry] = {}
        self._entries_lock = asyncio.Lock()

    async def run(self, conversation_id: str, operation: Callable[[], Awaitable[T]]) -> T:
        async with self._entries_lock:
            entry = self._entries.setdefault(conversation_id, _LockEntry())
            entry.users += 1
        try:
            async with entry.lock:
                return await operation()
        finally:
            async with self._entries_lock:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(conversation_id, None)
