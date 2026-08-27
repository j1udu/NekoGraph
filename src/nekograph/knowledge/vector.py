"""Optional FAISS index; sparse search remains the supported fallback."""

# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false

from __future__ import annotations

from pathlib import Path
from typing import Any


class FaissUnavailable(RuntimeError):
    """FAISS is not installed in this deployment."""


class FaissVectorIndex:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._index: Any = None
        self._faiss: Any = None

    def _load_module(self) -> None:
        if self._faiss is not None:
            return
        try:
            import faiss  # type: ignore[import-not-found]
        except ImportError as exc:
            raise FaissUnavailable(
                "install the optional faiss-cpu dependency to enable vectors"
            ) from exc
        self._faiss = faiss

    def build(self, vectors: list[list[float]]) -> None:
        if not vectors:
            self._index = None
            return
        self._load_module()
        import numpy as np  # type: ignore[import-not-found]

        matrix = np.asarray(vectors, dtype="float32")
        faiss = self._faiss
        faiss.normalize_L2(matrix)
        self._index = faiss.IndexFlatIP(matrix.shape[1])
        self._index.add(matrix)

    def save(self) -> None:
        if self._index is None:
            return
        self._load_module()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._faiss.write_index(self._index, str(self.path))

    def load(self) -> None:
        self._load_module()
        if self.path.exists():
            self._index = self._faiss.read_index(str(self.path))

    def search(self, vector: list[float], limit: int) -> list[tuple[int, float]]:
        if self._index is None:
            return []
        import numpy as np  # type: ignore[import-not-found]

        query = np.asarray([vector], dtype="float32")
        self._faiss.normalize_L2(query)
        scores, indices = self._index.search(query, limit)
        return [
            (int(index), float(score))
            for index, score in zip(indices[0], scores[0], strict=True)
            if index >= 0
        ]
