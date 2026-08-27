"""OpenAI-compatible/Cohere-shaped reranking provider."""

# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

import httpx


class RerankerProvider(Protocol):
    model: str

    async def rerank(self, query: str, documents: Sequence[str], limit: int) -> list[float]: ...


class OpenAICompatibleReranker:
    """Call providers exposing the common ``/rerank`` JSON contract."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def rerank(self, query: str, documents: Sequence[str], limit: int) -> list[float]:
        if not documents:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/rerank",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "query": query,
                    "documents": list(documents),
                    "top_n": limit,
                    "return_documents": False,
                },
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("results")
        if not isinstance(items, list):
            raise ValueError("rerank response results is invalid")
        scores = [0.0] * len(documents)
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("rerank result item is invalid")
            index = item.get("index")
            score = item.get("relevance_score", item.get("score"))
            if not isinstance(index, int) or not 0 <= index < len(documents):
                raise ValueError("rerank result index is invalid")
            if not isinstance(score, (int, float)):
                raise ValueError("rerank result score is invalid")
            scores[index] = float(score)
        return scores
