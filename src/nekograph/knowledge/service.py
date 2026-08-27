"""Application service keeping ingestion and retrieval behind one boundary."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from nekograph.knowledge.chunking import chunk_document
from nekograph.knowledge.models import Document, KnowledgeCollection, KnowledgeSearchResult
from nekograph.knowledge.parsers import ParsedDocument, parse_text, parse_url
from nekograph.knowledge.reranker import RerankerProvider
from nekograph.knowledge.retrieval import EmbeddingProvider, KnowledgeRetriever
from nekograph.knowledge.store import KnowledgeStore
from nekograph.knowledge.vector import FaissVectorIndex


class KnowledgeService:
    def __init__(
        self,
        store: KnowledgeStore,
        embedding: EmbeddingProvider | None = None,
        vector_index: FaissVectorIndex | None = None,
        reranker: RerankerProvider | None = None,
    ) -> None:
        self.store = store
        self.retriever = KnowledgeRetriever(store, embedding, vector_index, reranker)

    @classmethod
    async def open(
        cls,
        path: Path,
        embedding: EmbeddingProvider | None = None,
        vector_index_path: Path | None = None,
        reranker: RerankerProvider | None = None,
    ) -> KnowledgeService:
        vector_index = FaissVectorIndex(vector_index_path) if vector_index_path else None
        return cls(await KnowledgeStore.open(path), embedding, vector_index, reranker)

    @classmethod
    @asynccontextmanager
    async def lifespan(
        cls,
        path: Path,
        embedding: EmbeddingProvider | None = None,
        vector_index_path: Path | None = None,
        reranker: RerankerProvider | None = None,
    ) -> AsyncGenerator[KnowledgeService]:
        service = await cls.open(path, embedding, vector_index_path, reranker)
        try:
            yield service
        finally:
            await service.close()

    async def close(self) -> None:
        await self.store.close()

    async def ensure_collection(self, name: str, description: str = "") -> KnowledgeCollection:
        return await self.store.ensure_collection(
            KnowledgeCollection(name=name, description=description)
        )

    async def collections(self) -> tuple[KnowledgeCollection, ...]:
        return await self.store.list_collections()

    async def delete_collection(self, name: str) -> None:
        await self.store.delete_collection(name)

    async def ingest(self, collection: str, document: ParsedDocument) -> Document:
        await self.ensure_collection(collection)
        return await self.store.upsert_document(
            collection,
            document.title,
            document.source,
            document.content,
            document.source_url,
            chunk_document(document.content),
        )

    async def ingest_text(
        self, collection: str, *, title: str, source: str, content: str
    ) -> Document:
        return await self.ingest(collection, parse_text(content, title=title, source=source))

    async def ingest_url(self, collection: str, url: str) -> Document:
        return await self.ingest(collection, await parse_url(url))

    async def documents(self, collection: str) -> tuple[Document, ...]:
        return await self.store.list_documents(collection)

    async def delete_document(self, document_id: str) -> None:
        await self.store.delete_document(document_id)

    async def search(
        self, collection: str, query: str, limit: int = 5
    ) -> tuple[KnowledgeSearchResult, ...]:
        return await self.retriever.search(collection=collection, query=query, limit=limit)

    async def rebuild(self, collection: str | None = None) -> None:
        # Collection is reserved for a future per-index implementation. FTS5
        # rebuild is atomic and cheap for the first local SQLite deployment.
        del collection
        await self.store.rebuild_sparse_index()
