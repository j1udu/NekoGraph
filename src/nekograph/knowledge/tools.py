"""Knowledge retrieval tool exposed to the LangGraph agent."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from nekograph.knowledge.service import KnowledgeService
from nekograph.tools.models import JsonValue, ToolDefinition, ToolRisk
from nekograph.tools.registry import ToolRegistry


class SearchKnowledgeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2_000)
    collection: str = Field(default="yousa", min_length=1, max_length=80)
    limit: int = Field(default=5, ge=1, le=20)


def register_knowledge_tool(registry: ToolRegistry, service: KnowledgeService) -> None:
    async def search(arguments: BaseModel) -> JsonValue:
        parsed = SearchKnowledgeArgs.model_validate(arguments)
        results = await service.search(parsed.collection, parsed.query, parsed.limit)
        if not results:
            return {"found": False, "results": [], "reason": "no relevant knowledge found"}
        return {
            "found": True,
            "results": [
                {
                    "content": result.content,
                    "title": result.title,
                    "heading_path": result.heading_path,
                    "source": result.source,
                    "source_url": result.source_url,
                    "score": result.score,
                    "retrieval_method": result.retrieval_method,
                }
                for result in results
            ],
        }

    registry.register(
        ToolDefinition(
            name="search_knowledge",
            description=(
                "Search the NekoGraph topic knowledge base. Use only for stable explanatory "
                "materials such as yousa biography, works, and activity notes; do not use it "
                "for real-time Bilibili, QQ, subscription, or group-management facts. "
                "Answer only from returned passages and say when no relevant knowledge was found."
            ),
            args_schema=SearchKnowledgeArgs,
            handler=search,
            source="core.knowledge",
            risk=ToolRisk.SAFE,
            timeout_seconds=10.0,
        )
    )
