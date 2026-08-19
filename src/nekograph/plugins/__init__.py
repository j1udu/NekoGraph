"""Small, controlled plugin API for local NekoGraph extensions."""

from nekograph.plugins.runtime import (
    Plugin,
    PluginContext,
    PluginManager,
    PluginMetadata,
    PluginStatus,
)

__all__ = [
    "Plugin",
    "PluginContext",
    "PluginManager",
    "PluginMetadata",
    "PluginStatus",
]
