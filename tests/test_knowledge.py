from pathlib import Path

import pytest

from nekograph.knowledge.chunking import chunk_document
from nekograph.knowledge.service import KnowledgeService


@pytest.mark.asyncio
async def test_knowledge_ingest_search_update_delete_and_restart(tmp_path: Path) -> None:
    database = tmp_path / "knowledge.sqlite"
    service = await KnowledgeService.open(database)
    await service.ensure_collection("yousa", "专题资料")
    document = await service.ingest_text(
        "yousa",
        title="人物介绍",
        source="intro.md",
        content="# 泠鸢 yousa\n\n泠鸢是一位歌手和音乐创作者。",
    )
    assert document.chunk_count == 1
    results = await service.search("yousa", "音乐创作者")
    assert len(results) == 1
    assert results[0].heading_path == "泠鸢 yousa"
    same = await service.ingest_text(
        "yousa",
        title="人物介绍",
        source="intro.md",
        content="# 泠鸢 yousa\n\n泠鸢是一位歌手和音乐创作者。",
    )
    assert same.document_id == document.document_id
    updated = await service.ingest_text(
        "yousa",
        title="人物介绍",
        source="intro.md",
        content="# 泠鸢 yousa\n\n新的创作资料。",
    )
    assert updated.document_id == document.document_id
    assert await service.search("yousa", "音乐创作者") == ()
    await service.close()

    restarted = await KnowledgeService.open(database)
    assert len(await restarted.search("yousa", "新的创作")) == 1
    await restarted.delete_document(document.document_id)
    assert await restarted.search("yousa", "新的创作") == ()
    await restarted.close()


def test_chunk_document_preserves_heading_path() -> None:
    chunks = chunk_document("# 人物\n\n简介\n## 音乐\n\n作品风格")
    assert [item.heading_path for item in chunks] == ["人物", "人物 / 音乐"]


@pytest.mark.asyncio
async def test_collection_filter_prevents_cross_collection_recall(tmp_path: Path) -> None:
    service = await KnowledgeService.open(tmp_path / "knowledge.sqlite")
    await service.ingest_text("yousa", title="Y", source="y.md", content="泠鸢专题资料")
    await service.ingest_text("other", title="O", source="o.md", content="另一专题资料")
    assert await service.search("yousa", "另一专题") == ()
    await service.close()
