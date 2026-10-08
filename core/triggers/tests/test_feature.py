"""Tests for TriggerFeature — CRUD tools via BaseFeature."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path  # noqa: TC003
from typing import Any
from unittest.mock import AsyncMock

import pytest

from core.triggers.feature import TriggerFeature, TriggerFeatureContext
from core.triggers.registry import TriggerRegistry
from core.triggers.store import TriggerStore
from sdk.alfred_sdk.feature import BaseFeature, tool
from shared.streams import EVENTS_MAXLEN, EVENTS_STREAM, USER_TIMEZONE_KEY


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


@pytest.fixture
def mock_store() -> AsyncMock:
    store = AsyncMock(spec_set=TriggerStore)
    store.list_all = AsyncMock(return_value=[])
    store.get = AsyncMock(return_value=None)
    return store


@pytest.fixture
def snapshot_dir(tmp_path: Path) -> Path:
    d = tmp_path / "triggers"
    d.mkdir()
    return d


def test_feature_name() -> None:
    from core.triggers.feature import TriggerFeature

    f = TriggerFeature.__new__(TriggerFeature)
    assert f.feature_name == "triggers"


def test_get_tools_includes_crud() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore)))
    tools = f.get_tools()
    tool_names = [t.name for t in tools]
    assert any("create_trigger" in n for n in tool_names)
    assert any("list_triggers" in n for n in tool_names)
    assert any("delete_trigger" in n for n in tool_names)
    assert any("toggle_trigger" in n for n in tool_names)
    assert any("update_trigger" in n for n in tool_names)


def test_dynamic_description_includes_trigger_types() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore)))
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


@pytest.mark.asyncio
async def test_toggle_trigger(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    # Create a trigger to toggle
    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        enabled=True,
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.toggle_trigger(trigger_id="t-1", enabled=False)
    assert result["enabled"] is False
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_update_trigger_conditions(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(
        trigger_id="t-1",
        conditions={"cron": "0 8 * * *"},
        name="updated",
    )
    assert result["name"] == "updated"
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_update_trigger_not_found(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    mock_store.get = AsyncMock(return_value=None)
    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(trigger_id="t-999", name="x")
    assert "error" in result


@pytest.mark.asyncio
async def test_create_trigger_with_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="urgent reminder",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        urgency="urgent",
    )
    assert result["urgency"] == "urgent"
    mock_store.save.assert_called_once()


@pytest.mark.asyncio
async def test_create_trigger_default_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="chill reminder",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
    )
    assert result["urgency"] == "informational"


@pytest.mark.asyncio
async def test_create_trigger_invalid_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(
        name="test",
        trigger_type="time",
        conditions={"cron": "0 7 * * *"},
        urgency="nonexistent",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_update_trigger_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(trigger_id="t-1", urgency="important")
    assert result["urgency"] == "important"


@pytest.mark.asyncio
async def test_update_trigger_invalid_urgency(mock_store: AsyncMock) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    cls = TriggerRegistry.get("time")
    trigger = cls(
        trigger_id="t-1",
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
    )
    mock_store.get = AsyncMock(return_value=trigger)

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.update_trigger(trigger_id="t-1", urgency="nonexistent")
    assert "error" in result


def test_dynamic_description_includes_urgency() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore)))
    tools = f.get_tools()
    create_tool = next(t for t in tools if "create_trigger" in t.name)
    assert "urgency" in create_tool.description
    assert "informational" in create_tool.description
    assert "important" in create_tool.description
    assert "urgent" in create_tool.description


@pytest.mark.asyncio
async def test_create_trigger_localizes_naive_run_at(fake_redis: Any, snapshot_dir: Path) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext
    from core.triggers.types.time import TimeTrigger

    fake_redis.kv[USER_TIMEZONE_KEY] = "America/Denver"
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    result = await feature.create_trigger(
        name="tea time",
        trigger_type="time",
        conditions={"run_at": "2026-07-16T15:00:00"},
    )
    assert "error" not in result
    stored = await store.get(result["trigger_id"])
    assert isinstance(stored, TimeTrigger)
    run_at = stored.conditions.run_at
    assert run_at is not None and run_at.utcoffset() == timedelta(hours=-6)


@pytest.mark.asyncio
async def test_update_trigger_localizes_naive_run_at(fake_redis: Any, snapshot_dir: Path) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext
    from core.triggers.types.time import TimeTrigger

    fake_redis.kv[USER_TIMEZONE_KEY] = "America/Denver"
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    created = await feature.create_trigger(
        name="tea time",
        trigger_type="time",
        conditions={"run_at": "2026-07-16T15:00:00+00:00"},
    )
    updated = await feature.update_trigger(
        trigger_id=created["trigger_id"],
        conditions={"run_at": "2026-07-16T16:00:00"},
    )
    assert "error" not in updated
    stored = await store.get(created["trigger_id"])
    assert isinstance(stored, TimeTrigger)
    run_at = stored.conditions.run_at
    assert run_at is not None and run_at.utcoffset() == timedelta(hours=-6)


# --- run_in_seconds: the model asks for a delay, the server does the clock arithmetic ---


def _events(fake_redis: Any, event_type: str) -> list[dict[str, Any]]:
    events = [json.loads(e["event"]) for e in fake_redis.streams.get(EVENTS_STREAM, [])]
    return [e for e in events if e["event_type"] == event_type]


def test_dynamic_description_offers_run_in_seconds() -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore)))
    create_tool = next(t for t in f.get_tools() if "create_trigger" in t.name)
    assert "run_in_seconds" in create_tool.description
    assert "PREFER this over run_at" in create_tool.description


@pytest.mark.asyncio
async def test_create_trigger_run_in_seconds_stores_aware_run_at_and_fires(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    """The LLM tool call {"run_in_seconds": 5} lands as an aware run_at ~now+5s, and the
    scheduler arms its alarm for exactly that instant."""
    from core.triggers.engine import TriggerEngine
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext
    from core.triggers.types.time import TimeTrigger

    fake_redis.kv[USER_TIMEZONE_KEY] = "Asia/Kolkata"  # no DST: a date-independent offset
    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    before = datetime.now(UTC)
    result = await feature.create_trigger(
        name="tea", trigger_type="time", conditions={"run_in_seconds": 5}, one_shot=True
    )
    after = datetime.now(UTC)

    assert "error" not in result
    assert "run_in_seconds" not in result["conditions"]
    stored = await store.get(result["trigger_id"])
    assert isinstance(stored, TimeTrigger)
    run_at = stored.conditions.run_at
    assert run_at is not None
    assert run_at.utcoffset() == timedelta(hours=5, minutes=30)
    assert before + timedelta(seconds=5) <= run_at <= after + timedelta(seconds=5)
    # The bus sees the resolved form too, never the relative input.
    (created,) = _events(fake_redis, "trigger_created")
    assert "run_in_seconds" not in created["conditions"]
    assert datetime.fromisoformat(created["conditions"]["run_at"]) == run_at

    engine = TriggerEngine(store=store, redis=fake_redis)
    assert await engine.next_wakeup(after) == run_at
    await engine.evaluate_tick(run_at - timedelta(milliseconds=1))
    assert _events(fake_redis, "trigger_fired") == []
    await engine.evaluate_tick(run_at)
    (fired,) = _events(fake_redis, "trigger_fired")
    assert fired["trigger_id"] == result["trigger_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conditions",
    [
        {"run_at": "2026-07-16T15:00:00+00:00", "run_in_seconds": 5},
        {"cron": "0 7 * * *", "run_in_seconds": 5},
        {"cron": "0 7 * * *", "run_at": "2026-07-16T15:00:00+00:00"},
    ],
)
async def test_create_trigger_rejects_two_schedule_fields(
    mock_store: AsyncMock, conditions: dict[str, Any]
) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    f = TriggerFeature(ctx=TriggerFeatureContext(store=mock_store))
    result = await f.create_trigger(name="ambiguous", trigger_type="time", conditions=conditions)
    assert "only one of cron, run_at, run_in_seconds" in result["error"]
    mock_store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_trigger_run_in_seconds_reschedules_from_now(
    fake_redis: Any, snapshot_dir: Path
) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext
    from core.triggers.types.time import TimeTrigger

    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))
    created = await feature.create_trigger(
        name="stretch", trigger_type="time", conditions={"cron": "0 7 * * *"}
    )
    before = datetime.now(UTC)
    updated = await feature.update_trigger(
        trigger_id=created["trigger_id"], conditions={"run_in_seconds": 600}
    )
    after = datetime.now(UTC)

    assert "error" not in updated
    stored = await store.get(created["trigger_id"])
    assert isinstance(stored, TimeTrigger)
    assert stored.conditions.cron is None
    run_at = stored.conditions.run_at
    assert run_at is not None
    assert before + timedelta(seconds=600) <= run_at <= after + timedelta(seconds=600)


@pytest.mark.asyncio
async def test_trigger_created_caps_the_events_stream(fake_redis: Any, snapshot_dir: Path) -> None:
    from core.triggers.feature import TriggerFeature, TriggerFeatureContext

    store = TriggerStore(redis=fake_redis, snapshot_dir=snapshot_dir)
    feature = TriggerFeature(TriggerFeatureContext(store=store, redis=fake_redis))

    result = await feature.create_trigger(
        name="tea", trigger_type="time", conditions={"run_in_seconds": 5}, one_shot=True
    )

    assert "error" not in result
    assert [o for o in fake_redis.xadd_options if o["stream"] == EVENTS_STREAM] == [
        {"stream": EVENTS_STREAM, "maxlen": EVENTS_MAXLEN, "approximate": True}
    ]


class _TaggedTriggerFeature(TriggerFeature):
    """create_trigger tagged with a non-default audience and risk."""

    @tool(audience="reflex", risk="critical")
    async def create_trigger(
        self,
        name: str,
        trigger_type: str,
        conditions: dict[str, Any],
        action: dict[str, Any] | None = None,
        one_shot: bool = False,
        urgency: str = "informational",
    ) -> dict[str, Any]:
        """Create a new trigger."""
        return await super().create_trigger(
            name, trigger_type, conditions, action, one_shot, urgency
        )


def test_create_trigger_enrichment_keeps_audience_risk_and_schema() -> None:
    feature = _TaggedTriggerFeature(
        ctx=TriggerFeatureContext(store=AsyncMock(spec_set=TriggerStore))
    )
    tools = {t.name: t for t in feature.get_tools()}
    meta = tools["triggers.create_trigger"]
    base = {t.name: t for t in BaseFeature.get_tools(feature)}["triggers.create_trigger"]
    assert (meta.audience, meta.risk) == ("reflex", "critical")
    assert "informational" in meta.description  # the enrichment still ran
    assert meta.input_schema["required"] == ["name", "trigger_type", "conditions"]
    # Only the signature-generated schema carries defaults; one re-derived from
    # `parameters` would not.
    assert meta.input_schema["properties"]["urgency"]["default"] == "informational"
    assert meta.input_schema == base.input_schema
    assert meta.parameters == base.parameters
