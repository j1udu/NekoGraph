"""SQLite metadata, FTS5 index, and durable document lifecycle."""

# ruff: noqa: E501

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import aiosqlite

from nekograph.knowledge.chunking import ChunkInput, content_hash
from nekograph.knowledge.models import Document, DocumentChunk, KnowledgeCollection


def _tokens(text: str) -> str:
    # FTS5's unicode tokenizer does not segment Chinese; indexing CJK characters
    # gives predictable recall without requiring a native jieba extension.
    words = re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]", text.lower())
    return " ".join(words)


class KnowledgeStore:
    def __init__(self, connection: aiosqlite.Connection, path: Path) -> None:
        self.connection = connection
        self.path = path

    @classmethod
    async def open(cls, path: Path) -> KnowledgeStore:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = await aiosqlite.connect(path)
        connection.row_factory = aiosqlite.Row
        store = cls(connection, path)
        await store.setup()
        return store

    async def close(self) -> None:
        await self.connection.close()

    async def setup(self) -> None:
        await self.connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS knowledge_collections (
                name TEXT PRIMARY KEY, description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                document_id TEXT PRIMARY KEY, collection TEXT NOT NULL REFERENCES knowledge_collections(name) ON DELETE CASCADE,
                title TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT,
                content TEXT NOT NULL, content_hash TEXT NOT NULL, content_length INTEGER NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                UNIQUE(collection, source)
            );
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                chunk_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES knowledge_documents(document_id) ON DELETE CASCADE,
                collection TEXT NOT NULL, title TEXT NOT NULL, source TEXT NOT NULL, source_url TEXT,
                heading_path TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                ordinal INTEGER NOT NULL, metadata TEXT NOT NULL DEFAULT '{}'
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_chunks_fts USING fts5(
                chunk_id UNINDEXED, collection UNINDEXED, content, heading_path, title
            );
            CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_collection ON knowledge_chunks(collection);
            """
        )
        await self.connection.commit()

    async def ensure_collection(self, collection: KnowledgeCollection) -> KnowledgeCollection:
        await self.connection.execute(
            "INSERT OR IGNORE INTO knowledge_collections(name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (
                collection.name,
                collection.description,
                collection.created_at.isoformat(),
                collection.updated_at.isoformat(),
            ),
        )
        await self.connection.commit()
        return collection

    async def list_collections(self) -> tuple[KnowledgeCollection, ...]:
        cursor = await self.connection.execute("SELECT * FROM knowledge_collections ORDER BY name")
        rows = await cursor.fetchall()
        return tuple(
            KnowledgeCollection(
                name=row["name"],
                description=row["description"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def delete_collection(self, name: str) -> None:
        await self.connection.execute(
            "DELETE FROM knowledge_chunks_fts WHERE collection = ?", (name,)
        )
        await self.connection.execute("DELETE FROM knowledge_collections WHERE name = ?", (name,))
        await self.connection.commit()

    async def upsert_document(
        self,
        collection: str,
        title: str,
        source: str,
        content: str,
        source_url: str | None,
        chunks: Sequence[ChunkInput],
    ) -> Document:
        now = datetime.now(UTC).isoformat()
        digest = content_hash(content)
        cursor = await self.connection.execute(
            "SELECT document_id, content_hash, created_at FROM knowledge_documents WHERE collection = ? AND source = ?",
            (collection, source),
        )
        existing = await cursor.fetchone()
        document_id = str(existing["document_id"]) if existing else uuid4().hex
        created_at = str(existing["created_at"]) if existing else now
        if existing and existing["content_hash"] == digest:
            return await self.get_document(document_id)
        if existing:
            old_chunks = await self.connection.execute_fetchall(
                "SELECT chunk_id FROM knowledge_chunks WHERE document_id = ?", (document_id,)
            )
            await self.connection.executemany(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?",
                [(row["chunk_id"],) for row in old_chunks],
            )
            await self.connection.execute(
                "DELETE FROM knowledge_documents WHERE document_id = ?", (document_id,)
            )
        await self.connection.execute(
            "INSERT INTO knowledge_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                document_id,
                collection,
                title,
                source,
                source_url,
                content,
                digest,
                len(content),
                len(chunks),
                created_at,
                now,
            ),
        )
        for chunk in chunks:
            chunk_id = uuid4().hex
            await self.connection.execute(
                "INSERT INTO knowledge_chunks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    document_id,
                    collection,
                    title,
                    source,
                    source_url,
                    chunk.heading_path,
                    chunk.content,
                    content_hash(chunk.content),
                    chunk.ordinal,
                    json.dumps({}, ensure_ascii=False),
                ),
            )
            await self.connection.execute(
                "INSERT INTO knowledge_chunks_fts(chunk_id, collection, content, heading_path, title) VALUES (?, ?, ?, ?, ?)",
                (
                    chunk_id,
                    collection,
                    _tokens(chunk.content),
                    _tokens(chunk.heading_path),
                    _tokens(title),
                ),
            )
        await self.connection.commit()
        return await self.get_document(document_id)

    async def get_document(self, document_id: str) -> Document:
        cursor = await self.connection.execute(
            "SELECT * FROM knowledge_documents WHERE document_id = ?", (document_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError(document_id)
        return Document(
            document_id=row["document_id"],
            collection=row["collection"],
            title=row["title"],
            source=row["source"],
            source_url=row["source_url"],
            content_hash=row["content_hash"],
            content_length=row["content_length"],
            chunk_count=row["chunk_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def list_documents(self, collection: str) -> tuple[Document, ...]:
        cursor = await self.connection.execute(
            "SELECT * FROM knowledge_documents WHERE collection = ? ORDER BY updated_at DESC",
            (collection,),
        )
        rows = await cursor.fetchall()
        return tuple(
            Document(
                document_id=row["document_id"],
                collection=row["collection"],
                title=row["title"],
                source=row["source"],
                source_url=row["source_url"],
                content_hash=row["content_hash"],
                content_length=row["content_length"],
                chunk_count=row["chunk_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        )

    async def delete_document(self, document_id: str) -> None:
        rows = await self.connection.execute_fetchall(
            "SELECT chunk_id FROM knowledge_chunks WHERE document_id = ?", (document_id,)
        )
        for row in rows:
            await self.connection.execute(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?", (row["chunk_id"],)
            )
        await self.connection.execute(
            "DELETE FROM knowledge_documents WHERE document_id = ?", (document_id,)
        )
        await self.connection.commit()

    async def search_sparse(self, collection: str, query: str, limit: int) -> list[DocumentChunk]:
        terms = _tokens(query)
        if not terms:
            return []
        cursor = await self.connection.execute(
            """SELECT c.*, bm25(knowledge_chunks_fts) AS rank FROM knowledge_chunks_fts f
               JOIN knowledge_chunks c ON c.chunk_id = f.chunk_id
               WHERE f.collection = ? AND knowledge_chunks_fts MATCH ? ORDER BY rank LIMIT ?""",
            (collection, terms, limit),
        )
        rows = await cursor.fetchall()
        return [
            DocumentChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                collection=row["collection"],
                title=row["title"],
                source=row["source"],
                source_url=row["source_url"],
                heading_path=row["heading_path"],
                content=row["content"],
                content_hash=row["content_hash"],
                ordinal=row["ordinal"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def list_chunks(self, collection: str) -> list[DocumentChunk]:
        rows = await self.connection.execute_fetchall(
            "SELECT * FROM knowledge_chunks WHERE collection = ? ORDER BY ordinal", (collection,)
        )
        return [
            DocumentChunk(
                chunk_id=row["chunk_id"],
                document_id=row["document_id"],
                collection=row["collection"],
                title=row["title"],
                source=row["source"],
                source_url=row["source_url"],
                heading_path=row["heading_path"],
                content=row["content"],
                content_hash=row["content_hash"],
                ordinal=row["ordinal"],
                metadata=json.loads(row["metadata"]),
            )
            for row in rows
        ]

    async def rebuild_sparse_index(self) -> None:
        await self.connection.execute("DELETE FROM knowledge_chunks_fts")
        rows = await self.connection.execute_fetchall(
            "SELECT chunk_id, collection, content, heading_path, title FROM knowledge_chunks"
        )
        await self.connection.executemany(
            "INSERT INTO knowledge_chunks_fts(chunk_id, collection, content, heading_path, title) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    row["chunk_id"],
                    row["collection"],
                    _tokens(row["content"]),
                    _tokens(row["heading_path"]),
                    _tokens(row["title"]),
                )
                for row in rows
            ],
        )
        await self.connection.commit()
