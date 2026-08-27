"""Sparse and optional semantic retrieval primitives."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from nekograph.knowledge.models import DocumentChunk, KnowledgeSearchResult
from nekograph.knowledge.reranker import RerankerProvider
from nekograph.knowledge.store import KnowledgeStore
from nekograph.knowledge.vector import FaissVectorIndex


class EmbeddingProvider(Protocol):
    model: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class KnowledgeRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        embedding: EmbeddingProvider | None = None,
        vector_index: FaissVectorIndex | None = None,
        reranker: RerankerProvider | None = None,
    ) -> None:
        self.store = store
        self.embedding = embedding
        self.vector_index = vector_index
        self.reranker = reranker

    async def search(
        self, *, collection: str, query: str, limit: int = 5
    ) -> tuple[KnowledgeSearchResult, ...]:
        if not query.strip() or limit < 1:
            return ()
        sparse = await self.store.search_sparse(collection, query, max(limit * 3, limit))
        ranked: list[tuple[DocumentChunk, float, str]] = [
            (chunk, 1.0 / (index + 1), "sparse") for index, chunk in enumerate(_dedupe(sparse))
        ]
        if self.embedding is not None:
            try:
                chunks = await self.store.list_chunks(collection)
                vectors = await self.embedding.embed([chunk.content for chunk in chunks])
                query_vector = (await self.embedding.embed([query]))[0]
                if self.vector_index is not None:
                    try:
                        self.vector_index.build(vectors)
                        positions = self.vector_index.search(query_vector, max(limit * 3, limit))
                        semantic = [(chunks[index], score) for index, score in positions]
                    except Exception:
                        semantic = _cosine_rank(chunks, vectors, query_vector)
                else:
                    semantic = _cosine_rank(chunks, vectors, query_vector)
                ranks = {chunk.chunk_id: rank for rank, (chunk, _) in enumerate(semantic, 1)}
                sparse_ranks = {
                    chunk.chunk_id: rank for rank, (chunk, _, _) in enumerate(ranked, 1)
                }
                merged = {chunk.chunk_id: chunk for chunk in chunks}
                ranked = [
                    (
                        merged[chunk_id],
                        1 / (60 + sparse_ranks.get(chunk_id, 999))
                        + 1 / (60 + ranks.get(chunk_id, 999)),
                        "hybrid",
                    )
                    for chunk_id in merged
                    if chunk_id in sparse_ranks or chunk_id in ranks
                ]
                ranked.sort(key=lambda item: item[1], reverse=True)
            except Exception:
                pass
        if self.reranker is not None and ranked:
            try:
                candidates = ranked[: max(limit * 3, limit)]
                scores = await self.reranker.rerank(
                    query, [item[0].content for item in candidates], limit
                )
                ranked = [
                    (item[0], score, "reranked")
                    for item, score in zip(candidates, scores, strict=True)
                ]
                ranked.sort(key=lambda item: item[1], reverse=True)
            except Exception:
                pass
        return tuple(
            KnowledgeSearchResult(
                chunk_id=chunk.chunk_id,
                document_id=chunk.document_id,
                collection=chunk.collection,
                title=chunk.title,
                source=chunk.source,
                source_url=chunk.source_url,
                heading_path=chunk.heading_path,
                content=chunk.content,
                score=score,
                retrieval_method=method,
                metadata=chunk.metadata,
            )
            for chunk, score, method in ranked[:limit]
        )


def _dedupe(chunks: Sequence[DocumentChunk]) -> list[DocumentChunk]:
    seen: set[str] = set()
    result: list[DocumentChunk] = []
    for chunk in chunks:
        if chunk.content_hash in seen:
            continue
        seen.add(chunk.content_hash)
        result.append(chunk)
    return result


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(value * value for value in left) ** 0.5
    right_norm = sum(value * value for value in right) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def _cosine_rank(
    chunks: Sequence[DocumentChunk],
    vectors: Sequence[Sequence[float]],
    query_vector: Sequence[float],
) -> list[tuple[DocumentChunk, float]]:
    return sorted(
        (
            (chunk, _cosine(query_vector, vector))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ),
        key=lambda item: item[1],
        reverse=True,
    )
