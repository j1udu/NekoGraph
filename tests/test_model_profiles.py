from __future__ import annotations

import stat
from collections.abc import Sequence
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from pydantic import SecretStr, ValidationError

from nekograph.agent.model_controller import (
    ActiveModelInfo,
    ModelController,
    ModelHandle,
)
from nekograph.agent.profiles import (
    ActiveModelProfileError,
    DuplicateModelProfileError,
    ModelProfileInput,
    ModelProfileStore,
    ModelProfileUpdate,
    StoredModelProfile,
    profile_view,
)
from nekograph.model_types import ModelToolSpec


def profile(name: str, model: str = "test-model") -> ModelProfileInput:
    return ModelProfileInput(
        name=name,
        model=model,
        base_url="https://provider.example/v1",
        api_key=SecretStr(f"secret-{name}"),
        temperature=0.2,
        timeout_seconds=12,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"base_url": "ftp://provider.example"},
        {"base_url": "http://"},
        {"api_key": SecretStr("")},
    ],
)
def test_model_profile_input_rejects_invalid_connection_values(
    values: dict[str, object],
) -> None:
    data: dict[str, object] = {
        "name": "Primary",
        "model": "test-model",
        "base_url": "https://provider.example/v1",
        "api_key": SecretStr("secret"),
    }
    data.update(values)

    with pytest.raises(ValidationError):
        ModelProfileInput.model_validate(data)


async def test_profile_store_imports_multiple_profiles_and_hides_secrets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "models.sqlite"
    async with ModelProfileStore.open(database) as store:
        created = await store.create_many(
            [profile("Primary", "model-a"), profile("Backup", "model-b")]
        )
        listed = await store.list()

    assert len(created) == 2
    assert {item.name for item in listed} == {"Primary", "Backup"}
    assert all(not item.active for item in listed)
    assert "secret-Primary" not in repr(profile_view(created[0]))
    assert "api_key" not in profile_view(created[0]).model_dump()
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


async def test_profile_store_bulk_import_is_transactional(tmp_path: Path) -> None:
    async with ModelProfileStore.open(tmp_path / "models.sqlite") as store:
        await store.create_many([profile("Existing")])

        with pytest.raises(DuplicateModelProfileError):
            await store.create_many([profile("New"), profile("existing")])

        listed = await store.list()

    assert [item.name for item in listed] == ["Existing"]


async def test_active_profile_must_be_switched_before_delete(tmp_path: Path) -> None:
    async with ModelProfileStore.open(tmp_path / "models.sqlite") as store:
        (created,) = await store.create_many([profile("Primary")])
        await store.set_active(created.profile_id)

        with pytest.raises(ActiveModelProfileError):
            await store.delete(created.profile_id)

        await store.set_active(None)
        await store.delete(created.profile_id)
        assert await store.list() == ()


async def test_profile_store_updates_profile_and_preserves_api_key(tmp_path: Path) -> None:
    async with ModelProfileStore.open(tmp_path / "models.sqlite") as store:
        (created,) = await store.create_many([profile("Primary")])
        updated = await store.update(
            created.profile_id,
            ModelProfileUpdate(
                name="Renamed",
                model="updated-model",
                base_url="https://updated.example/v1",
                temperature=0.7,
                timeout_seconds=45,
            ),
        )

    assert updated.name == "Renamed"
    assert updated.model == "updated-model"
    assert updated.api_key.get_secret_value() == "secret-Primary"
    assert updated.temperature == 0.7
    assert updated.timeout_seconds == 45


async def test_profile_store_rejects_duplicate_name_on_update(tmp_path: Path) -> None:
    async with ModelProfileStore.open(tmp_path / "models.sqlite") as store:
        primary, backup = await store.create_many(
            [profile("Primary"), profile("Backup")]
        )

        with pytest.raises(DuplicateModelProfileError):
            await store.update(
                backup.profile_id,
                ModelProfileUpdate(
                    name=primary.name,
                    model=backup.model,
                    base_url=backup.base_url,
                    temperature=backup.temperature,
                    timeout_seconds=backup.timeout_seconds,
                ),
            )


class NamedModel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.closed = False

    async def complete(
        self,
        messages: Sequence[AnyMessage],
        tools: Sequence[ModelToolSpec] = (),
    ) -> AIMessage:
        return AIMessage(content=f"{self.name}:{messages[-1].content}")

    async def close(self) -> None:
        self.closed = True


async def test_model_controller_hot_switches_and_restores_fallback(tmp_path: Path) -> None:
    fallback = NamedModel("fallback")
    opened: list[NamedModel] = []

    def factory(stored: StoredModelProfile) -> ModelHandle:
        model = NamedModel(stored.model)
        opened.append(model)
        return ModelHandle(model=model, close=model.close)

    async with ModelProfileStore.open(tmp_path / "models.sqlite") as store:
        (created,) = await store.create_many([profile("Primary", "profile-model")])
        controller = ModelController(
            store=store,
            fallback=ModelHandle(fallback),
            fallback_info=ActiveModelInfo(
                profile_id=None,
                name="Environment fallback",
                model="fake",
                base_url=None,
                source="environment",
            ),
            profile_factory=factory,
        )
        await controller.initialize()

        before = await controller.complete([HumanMessage(content="one")])
        active = await controller.activate(created.profile_id)
        after = await controller.complete([HumanMessage(content="two")])
        restored = await controller.activate(None)
        final = await controller.complete([HumanMessage(content="three")])

    assert before.content == "fallback:one"
    assert active.profile_id == created.profile_id
    assert after.content == "profile-model:two"
    assert opened[0].closed is True
    assert restored.profile_id is None
    assert final.content == "fallback:three"


async def test_model_controller_restores_persisted_active_profile(tmp_path: Path) -> None:
    database = tmp_path / "models.sqlite"
    async with ModelProfileStore.open(database) as store:
        (created,) = await store.create_many([profile("Primary", "persisted-model")])
        await store.set_active(created.profile_id)

    async with ModelProfileStore.open(database) as store:
        controller = ModelController(
            store=store,
            fallback=ModelHandle(NamedModel("fallback")),
            fallback_info=ActiveModelInfo(
                profile_id=None,
                name="Fallback",
                model="fake",
                base_url=None,
                source="environment",
            ),
            profile_factory=lambda stored: ModelHandle(NamedModel(stored.model)),
        )
        await controller.initialize()
        response = await controller.complete([HumanMessage(content="hello")])

    assert controller.active_info.profile_id == created.profile_id
    assert response.content == "persisted-model:hello"
