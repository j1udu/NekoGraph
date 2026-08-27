"""Heading-aware chunking that preserves the document's local context."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkInput:
    heading_path: str
    content: str
    ordinal: int


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _markdown_sections(content: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    headings: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            if current:
                sections.append((" / ".join(headings), current))
                current = []
            level = len(match.group(1))
            headings = headings[: level - 1]
            headings.append(match.group(2))
        else:
            current.append(line)
    if current:
        sections.append((" / ".join(headings), current))
    return [
        (heading, "\n".join(lines).strip())
        for heading, lines in sections
        if "\n".join(lines).strip()
    ]


def chunk_document(
    content: str, *, max_chars: int = 900, overlap_chars: int = 120
) -> list[ChunkInput]:
    if max_chars < 100 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("invalid chunk size")
    sections = _markdown_sections(content)
    if not sections:
        sections = [("", content.strip())]
    chunks: list[ChunkInput] = []
    ordinal = 0
    for heading, section in sections:
        remaining = section
        while remaining:
            piece = remaining[:max_chars]
            if len(remaining) > max_chars:
                boundary = max(piece.rfind("\n"), piece.rfind("。"), piece.rfind(" "))
                if boundary >= max_chars // 2:
                    piece = piece[: boundary + (1 if piece[boundary] == "。" else 0)]
            piece = piece.strip()
            if piece:
                chunks.append(ChunkInput(heading_path=heading, content=piece, ordinal=ordinal))
                ordinal += 1
            if len(remaining) <= len(piece):
                break
            remaining = remaining[max(0, len(piece) - overlap_chars) :]
    return chunks
