"""Small, dependency-light parsers for the first knowledge ingestion surface."""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser

import httpx


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    title: str
    source: str
    content: str
    source_url: str | None = None


class _TextHTMLParser(HTMLParser):
    _ignored = {"script", "style", "noscript", "nav", "header", "footer", "aside"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._ignore_depth = 0
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._ignored:
            self._ignore_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._ignored and self._ignore_depth:
            self._ignore_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._ignore_depth:
            return
        value = re.sub(r"\s+", " ", data).strip()
        if not value:
            return
        if self._in_title:
            self.title = value
        self.parts.append(value)


def parse_text(
    content: str, *, title: str, source: str, source_url: str | None = None
) -> ParsedDocument:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        raise ValueError("document content is empty")
    return ParsedDocument(
        title=title.strip() or source, source=source, content=normalized, source_url=source_url
    )


async def parse_url(url: str, *, timeout_seconds: float = 20.0) -> ParsedDocument:
    if not re.fullmatch(r"https?://[^\s]+", url):
        raise ValueError("only http(s) URLs are supported")
    async with httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=True) as client:
        response = await client.get(url, headers={"User-Agent": "NekoGraph/0.1 knowledge importer"})
        response.raise_for_status()
    parser = _TextHTMLParser()
    parser.feed(response.text)
    content = "\n".join(parser.parts)
    return parse_text(content, title=parser.title or url, source=url, source_url=url)
