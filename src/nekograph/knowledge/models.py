"""Persistence and retrieval models for topic knowledge."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


def utc_now() -> datetime:
    return datetime.now(UTC)


class KnowledgeCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    description: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Document(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    collection: str
    title: str
    source: str
    source_url: str | None = None
    content_hash: str
    content_length: int
    chunk_count: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class DocumentChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    collection: str
    title: str
    source: str
    source_url: str | None = None
    heading_path: str = ""
    content: str
    content_hash: str
    ordinal: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    document_id: str
    collection: str
    title: str
    source: str
    source_url: str | None = None
    heading_path: str = ""
    content: str
    score: float
    retrieval_method: str
    metadata: dict[str, Any] = Field(default_factory=dict)
