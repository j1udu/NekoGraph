"""Neutral model-facing schema types shared by adapters and tool sources."""

from typing import Any, TypedDict


class ModelToolFunction(TypedDict):
    name: str
    description: str
    parameters: dict[str, Any]


class ModelToolSpec(TypedDict):
    type: str
    function: ModelToolFunction
