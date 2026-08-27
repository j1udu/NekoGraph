"""OpenAI-compatible embeddings with a deliberately small provider surface."""

# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownLambdaType=false

from __future__ import annotations

from collections.abc import Sequence

import httpx


class OpenAICompatibleEmbedding:
    def __init__(
        self, *, base_url: str, model: str, api_key: str, timeout_seconds: float = 30.0
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(
                f"{self.base_url}/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": list(texts)},
            )
            response.raise_for_status()
            payload = response.json()
        data = payload.get("data")
        if not isinstance(data, list):
            raise ValueError("embedding response data is invalid")
        vectors: list[list[float]] = []
        for item in sorted(data, key=lambda value: value.get("index", 0)):
            vector = item.get("embedding")
            if not isinstance(vector, list) or not all(
                isinstance(value, (int, float)) for value in vector
            ):
                raise ValueError("embedding vector is invalid")
            vectors.append([float(value) for value in vector])
        if len(vectors) != len(texts):
            raise ValueError("embedding response count does not match input")
        return vectors
