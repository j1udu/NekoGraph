from pathlib import Path

import pytest
from pydantic import SecretStr

from nekograph.knowledge.config_store import KnowledgeModelConfig, KnowledgeModelConfigStore


@pytest.mark.asyncio
async def test_knowledge_model_config_store_persists_and_redacts_views(tmp_path: Path) -> None:
    path = tmp_path / "knowledge-models.json"
    store = await KnowledgeModelConfigStore.open(path)
    await store.save(
        KnowledgeModelConfig(
            kind="embedding",
            base_url="https://provider.test/v1",
            model="embedding-model",
            api_key=SecretStr("secret-key"),
        )
    )
    assert store.views()["embedding"] == {
        "configured": True,
        "base_url": "https://provider.test/v1",
        "model": "embedding-model",
    }
    assert "secret-key" not in str(store.views())
    assert path.stat().st_mode & 0o777 == 0o600

    reopened = await KnowledgeModelConfigStore.open(path)
    reopened_config = reopened.get("embedding")
    assert reopened_config is not None
    assert reopened_config.api_key.get_secret_value() == "secret-key"
    await reopened.delete("embedding")
    assert reopened.views()["embedding"]["configured"] is False
