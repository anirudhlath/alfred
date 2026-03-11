"""Tests for TriggerFeature — CRUD tools via BaseFeature."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock()
    store.save = AsyncMock()
    store.delete = AsyncMock()
    store.list_all = AsyncMock(return_value=[])
    return store


def test_feature_name() -> None:
    from core.triggers.feature import TriggerFeature

    f = TriggerFeature.__new__(TriggerFeature)
    assert f.feature_name == "triggers"


def test_get_tools_includes_crud() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock()))
    tools = f.get_tools()
    tool_names = [t.name for t in tools]
    assert any("create_trigger" in n for n in tool_names)
    assert any("list_triggers" in n for n in tool_names)
    assert any("delete_trigger" in n for n in tool_names)
    assert any("toggle_trigger" in n for n in tool_names)
    assert any("update_trigger" in n for n in tool_names)


def test_dynamic_description_includes_trigger_types() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock()))
    tools = f.get_tools()
    create_tool = next(t for t in tools if "create_trigger" in t.name)
    assert "time" in create_tool.description
    assert "sensor" in create_tool.description
    assert "composite" in create_tool.description


@pytest.mark.asyncio
async def test_create_trigger(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
    )
    assert result["trigger_id"]
    assert result["trigger_type"] == "time"
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_create_trigger_with_action(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        action={"tool_name": "x", "target_service": "y", "parameters": {}},
    )
    assert result["action"] is not None


@pytest.mark.asyncio
async def test_create_trigger_invalid_type(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="nonexistent",
        conditions={},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_trigger(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.delete_trigger(trigger_id="t-1")
    mock_store.delete.assert_called_once_with("t-1")
    assert result["status"] == "deleted"
