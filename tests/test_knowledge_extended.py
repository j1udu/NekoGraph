from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage

from nekograph.agent import FakeChatModel, LangGraphRuntime
from nekograph.knowledge.chunking import chunk_document
from nekograph.knowledge.embedding import OpenAICompatibleEmbedding
from nekograph.knowledge.parsers import parse_text, parse_url
from nekograph.knowledge.reranker import OpenAICompatibleReranker
from nekograph.knowledge.service import KnowledgeService
from nekograph.knowledge.tools import register_knowledge_tool
from nekograph.knowledge.vector import FaissVectorIndex
from nekograph.models import Actor, Chat, ChatKind, ConversationRef, RunContext
from nekograph.tools import ToolExecutionContext, ToolRegistry, ToolResultCode


class FakeEmbedding:
    model = "fake-embedding"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            if "音乐" in text or "歌曲" in text:
                vectors.append([1.0, 0.0])
            elif "直播" in text:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


class BrokenEmbedding:
    model = "broken"

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        del texts
        raise RuntimeError("provider unavailable")


class FakeResponse:
    def __init__(self, text: str, url: str = "https://example.test/page") -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class FakeHttpClient:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.requested_url: str | None = None

    async def __aenter__(self) -> FakeHttpClient:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def get(self, url: str, **kwargs: object) -> FakeResponse:
        del kwargs
        self.requested_url = url
        return self.response


async def test_url_parser_removes_navigation_scripts_and_preserves_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = """
    <html><head><title>泠鸢资料</title><script>ignore()</script></head>
    <body><nav>导航</nav><main><h1>人物介绍</h1><p>音乐创作者。</p></main><footer>页脚</footer></body></html>
    """
    client = FakeHttpClient(FakeResponse(html))

    def client_factory(**kwargs: object) -> FakeHttpClient:
        del kwargs
        return client

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    parsed = await parse_url("https://example.test/page")
    assert parsed.title == "泠鸢资料"
    assert "人物介绍" in parsed.content
    assert "音乐创作者" in parsed.content
    assert "导航" not in parsed.content
    assert "ignore" not in parsed.content
    assert "页脚" not in parsed.content
    assert parsed.source_url == "https://example.test/page"


@pytest.mark.parametrize("url", ["file:///tmp/a.txt", "javascript:alert(1)", "not-url"])
async def test_url_parser_rejects_non_http_urls(url: str) -> None:
    with pytest.raises(ValueError, match="http"):
        await parse_url(url)


def test_text_parser_rejects_empty_content() -> None:
    with pytest.raises(ValueError, match="empty"):
        parse_text("  \n", title="empty", source="empty.txt")


def test_chunking_overlap_size_and_invalid_configuration() -> None:
    content = "# 长文\n\n" + "段落内容。" * 100
    chunks = chunk_document(content, max_chars=120, overlap_chars=20)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 120 for chunk in chunks)
    assert all(chunk.heading_path == "长文" for chunk in chunks)
    with pytest.raises(ValueError, match="invalid chunk size"):
        chunk_document(content, max_chars=100, overlap_chars=100)


async def test_hybrid_retrieval_uses_embedding_and_faiss_fallback(tmp_path: Path) -> None:
    service = await KnowledgeService.open(
        tmp_path / "knowledge.sqlite",
        FakeEmbedding(),
        tmp_path / "knowledge.faiss",
    )
    await service.ingest_text(
        "yousa", title="音乐", source="music.md", content="歌曲作品与音乐风格"
    )
    await service.ingest_text("yousa", title="直播", source="live.md", content="直播活动总结")
    results = await service.search("yousa", "歌曲相关内容", 2)
    assert results[0].title == "音乐"
    assert results[0].retrieval_method == "hybrid"
    await service.close()


async def test_hybrid_retrieval_falls_back_when_faiss_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = await KnowledgeService.open(
        tmp_path / "knowledge.sqlite",
        FakeEmbedding(),
        tmp_path / "knowledge.faiss",
    )
    await service.ingest_text(
        "yousa", title="音乐", source="music.md", content="歌曲作品与音乐风格"
    )

    def broken_build(self: FaissVectorIndex, vectors: list[list[float]]) -> None:
        del self, vectors
        raise RuntimeError("broken index")

    monkeypatch.setattr(FaissVectorIndex, "build", broken_build)
    results = await service.search("yousa", "歌曲相关内容")
    assert results[0].title == "音乐"
    assert results[0].retrieval_method == "hybrid"
    await service.close()


def test_faiss_index_build_save_load_and_search(tmp_path: Path) -> None:
    path = tmp_path / "knowledge.faiss"
    index = FaissVectorIndex(path)
    index.build([[1.0, 0.0], [0.0, 1.0]])
    assert index.search([1.0, 0.0], 2)[0][0] == 0
    index.save()
    assert path.is_file()

    restored = FaissVectorIndex(path)
    restored.load()
    assert restored.search([0.0, 1.0], 2)[0][0] == 1


async def test_embedding_failure_falls_back_to_sparse(tmp_path: Path) -> None:
    service = await KnowledgeService.open(tmp_path / "knowledge.sqlite", BrokenEmbedding())
    await service.ingest_text("yousa", title="人物", source="person.md", content="泠鸢人物经历")
    results = await service.search("yousa", "人物经历")
    assert len(results) == 1
    assert results[0].retrieval_method == "sparse"
    await service.close()


async def test_search_knowledge_tool_success_empty_and_validation(tmp_path: Path) -> None:
    service = await KnowledgeService.open(tmp_path / "knowledge.sqlite")
    await service.ingest_text("yousa", title="人物", source="person.md", content="泠鸢人物经历")
    registry = ToolRegistry()
    register_knowledge_tool(registry, service)
    context = ToolExecutionContext()

    found = await registry.execute(
        name="search_knowledge",
        arguments={"query": "人物经历", "collection": "yousa", "limit": 3},
        context=context,
    )
    assert found.code is ToolResultCode.SUCCESS
    assert isinstance(found.output, dict) and found.output["found"] is True

    empty = await registry.execute(
        name="search_knowledge",
        arguments={"query": "完全不存在", "collection": "yousa"},
        context=context,
    )
    assert isinstance(empty.output, dict) and empty.output["found"] is False

    invalid = await registry.execute(
        name="search_knowledge",
        arguments={"query": "", "limit": 100},
        context=context,
    )
    assert invalid.code is ToolResultCode.INVALID_ARGUMENTS
    await service.close()


async def test_openai_compatible_embedding_response_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class EmbeddingResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": [
                    {"index": 1, "embedding": [0, 1.0]},
                    {"index": 0, "embedding": [1, 0.0]},
                ]
            }

    class EmbeddingClient(FakeHttpClient):
        async def post(self, url: str, **kwargs: object) -> EmbeddingResponse:
            assert url == "https://provider.test/v1/embeddings"
            assert kwargs["json"] == {"model": "embed-model", "input": ["a", "b"]}
            return EmbeddingResponse()

    def embedding_client_factory(**kwargs: object) -> EmbeddingClient:
        del kwargs
        return EmbeddingClient(FakeResponse(""))

    monkeypatch.setattr(httpx, "AsyncClient", embedding_client_factory)
    provider = OpenAICompatibleEmbedding(
        base_url="https://provider.test/v1/",
        model="embed-model",
        api_key="secret",
    )
    assert await provider.embed(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"data": "invalid"}, "data is invalid"),
        ({"data": [{"index": 0, "embedding": "invalid"}]}, "vector is invalid"),
        ({"data": []}, "count does not match"),
    ],
)
async def test_openai_compatible_embedding_rejects_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
    error: str,
) -> None:
    class InvalidResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return payload

    class InvalidClient(FakeHttpClient):
        async def post(self, url: str, **kwargs: object) -> InvalidResponse:
            del url, kwargs
            return InvalidResponse()

    def invalid_client_factory(**kwargs: object) -> InvalidClient:
        del kwargs
        return InvalidClient(FakeResponse(""))

    monkeypatch.setattr(httpx, "AsyncClient", invalid_client_factory)
    provider = OpenAICompatibleEmbedding(
        base_url="https://provider.test/v1", model="embed-model", api_key="secret"
    )
    with pytest.raises(ValueError, match=error):
        await provider.embed(["a"])


async def test_openai_compatible_reranker_parses_scores_and_sends_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RerankResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "score": 0.2}]}

    class RerankClient(FakeHttpClient):
        async def post(self, url: str, **kwargs: object) -> RerankResponse:
            assert url == "https://provider.test/v1/rerank"
            assert kwargs["json"] == {
                "model": "rerank-model",
                "query": "音乐",
                "documents": ["片段一", "片段二"],
                "top_n": 2,
                "return_documents": False,
            }
            return RerankResponse()

    def rerank_client_factory(**kwargs: object) -> RerankClient:
        del kwargs
        return RerankClient(FakeResponse(""))

    monkeypatch.setattr(httpx, "AsyncClient", rerank_client_factory)
    provider = OpenAICompatibleReranker(
        base_url="https://provider.test/v1",
        model="rerank-model",
        api_key="secret",
    )
    assert await provider.rerank("音乐", ["片段一", "片段二"], 2) == [0.2, 0.9]


async def test_reranker_failure_keeps_hybrid_results(tmp_path: Path) -> None:
    service = await KnowledgeService.open(tmp_path / "knowledge.sqlite", FakeEmbedding())
    await service.ingest_text("yousa", title="音乐", source="music.md", content="歌曲音乐")

    class BrokenReranker:
        model = "broken"

        async def rerank(self, query: str, documents: Sequence[str], limit: int) -> list[float]:
            del query, documents, limit
            raise RuntimeError("reranker unavailable")

    service.retriever.reranker = BrokenReranker()
    results = await service.search("yousa", "歌曲")
    assert results[0].retrieval_method == "hybrid"
    await service.close()


async def test_langgraph_executes_search_knowledge_tool_end_to_end(tmp_path: Path) -> None:
    service = await KnowledgeService.open(tmp_path / "knowledge.sqlite")
    await service.ingest_text(
        "yousa", title="音乐", source="music.md", content="泠鸢的作品包含原创歌曲。"
    )
    registry = ToolRegistry()
    register_knowledge_tool(registry, service)
    model = FakeChatModel(
        scripted_responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "knowledge-call-1",
                        "name": "search_knowledge",
                        "args": {"query": "原创歌曲", "collection": "yousa", "limit": 3},
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="根据知识库，泠鸢拥有原创歌曲作品。"),
        ]
    )
    conversation = ConversationRef(conversation_id="rag-test", thread_id="rag-test")
    context = RunContext(
        run_id="run-rag",
        bot_id="10000",
        actor=Actor(user_id="20000", display_name="tester"),
        chat=Chat(kind=ChatKind.PRIVATE, chat_id="20000"),
        conversation=conversation,
    )
    async with LangGraphRuntime.open(
        checkpoint_path=tmp_path / "checkpoints.sqlite", model=model, tools=registry
    ) as runtime:
        answer = await runtime.respond(context, "泠鸢有哪些原创作品？")

    assert answer == "根据知识库，泠鸢拥有原创歌曲作品。"
    assert model.received_tools[0][0]["function"]["name"] == "search_knowledge"
    tool_messages = [item for item in model.received_snapshots[1] if item[0] == "tool"]
    assert len(tool_messages) == 1
    assert '"found":true' in tool_messages[0][1]
    assert "原创歌曲" in tool_messages[0][1]
    assert "post_type" not in tool_messages[0][1]
    await service.close()
