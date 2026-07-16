# core/triggers/tests/test_actions_consumer.py
"""Tests for the triggers-process ACTIONS_STREAM consumer (admin trigger mutations).

The triggers process owns TriggerStore, so admin fire/enable mutations are routed
to it via ACTIONS_STREAM (group 'triggers-internal') and applied through the real
TriggerEngine / TriggerStore — keeping Redis and the YAML snapshot consistent.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest
from core.triggers.registry import TriggerRegistry


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def _make_trigger(trigger_id: str = "t-1", *, enabled: bool = True) -> object:
    cls = TriggerRegistry.get("time")
    return cls(
        trigger_id=trigger_id,
        trigger_type="time",
        name="test",
        created_by="test",
        created_at=datetime.now(UTC),
        conditions={"cron": "0 7 * * *"},
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_fire_trigger_handler_calls_engine_fire_with_admin_provenance() -> None:
    from core.triggers.__main__ import _handle_fire_trigger

    trigger = _make_trigger("t-1")
    store = AsyncMock()
    store.get = AsyncMock(return_value=trigger)
    engine = AsyncMock()

    await _handle_fire_trigger(store, engine, {"trigger_id": "t-1"})

    store.get.assert_awaited_once_with("t-1")
    engine.fire.assert_awaited_once()
    args, kwargs = engine.fire.call_args
    assert args[0] is trigger
    assert kwargs.get("fired_by") == "admin"


@pytest.mark.asyncio
async def test_fire_trigger_handler_emits_admin_provenance_event_via_real_engine() -> None:
    """End-to-end: admin fire of a no-action trigger emits TriggerFired fired_by=admin."""
    from core.triggers.__main__ import _handle_fire_trigger
    from core.triggers.engine import TriggerEngine

    trigger = _make_trigger("t-1")  # no action → emits TriggerFired
    store = AsyncMock()
    store.get = AsyncMock(return_value=trigger)
    store.save = AsyncMock()
    store.delete = AsyncMock()
    redis = AsyncMock()
    redis.xadd = AsyncMock()
    redis.lpush = AsyncMock()
    engine = TriggerEngine(store=store, redis=redis)

    await _handle_fire_trigger(store, engine, {"trigger_id": "t-1"})

    stream, payload = redis.xadd.call_args[0]
    assert stream == "alfred:events"
    event = json.loads(payload["event"])
    assert event["event_type"] == "trigger_fired"
    assert event["fired_by"] == "admin"


@pytest.mark.asyncio
async def test_fire_trigger_handler_unknown_id_warns_and_skips() -> None:
    from core.triggers.__main__ import _handle_fire_trigger

    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    engine = AsyncMock()

    await _handle_fire_trigger(store, engine, {"trigger_id": "ghost"})

    engine.fire.assert_not_awaited()


@pytest.mark.asyncio
async def test_fire_trigger_handler_refreshes_cache_on_miss_then_proceeds() -> None:
    """get() returns None first (stale cache), then the trigger after refresh — fire proceeds."""
    from core.triggers.__main__ import _handle_fire_trigger

    trigger = _make_trigger("t-new")
    store = AsyncMock()
    # First call (cache miss), second call (after refresh) finds the trigger.
    store.get = AsyncMock(side_effect=[None, trigger])
    store.refresh = AsyncMock()
    engine = AsyncMock()

    await _handle_fire_trigger(store, engine, {"trigger_id": "t-new"})

    store.refresh.assert_awaited_once()
    engine.fire.assert_awaited_once()
    args, kwargs = engine.fire.call_args
    assert args[0] is trigger
    assert kwargs.get("fired_by") == "admin"


@pytest.mark.asyncio
async def test_set_trigger_enabled_handler_persists_via_store_save() -> None:
    from core.triggers.__main__ import _handle_set_trigger_enabled

    trigger = _make_trigger("t-1", enabled=True)
    store = AsyncMock()
    store.get = AsyncMock(return_value=trigger)
    store.save = AsyncMock()

    await _handle_set_trigger_enabled(store, {"trigger_id": "t-1", "enabled": False})

    store.save.assert_awaited_once()
    saved = store.save.call_args[0][0]
    assert saved.enabled is False
    assert saved.trigger_id == "t-1"


@pytest.mark.asyncio
async def test_set_trigger_enabled_handler_unknown_id_warns_and_skips() -> None:
    from core.triggers.__main__ import _handle_set_trigger_enabled

    store = AsyncMock()
    store.get = AsyncMock(return_value=None)
    store.save = AsyncMock()

    await _handle_set_trigger_enabled(store, {"trigger_id": "ghost", "enabled": True})

    store.save.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_trigger_enabled_handler_refreshes_cache_on_miss_then_proceeds() -> None:
    """get() returns None first (stale cache), then the trigger after refresh — save proceeds."""
    from core.triggers.__main__ import _handle_set_trigger_enabled

    trigger = _make_trigger("t-new", enabled=True)
    store = AsyncMock()
    # First call (cache miss), second call (after refresh) finds the trigger.
    store.get = AsyncMock(side_effect=[None, trigger])
    store.refresh = AsyncMock()
    store.save = AsyncMock()

    await _handle_set_trigger_enabled(store, {"trigger_id": "t-new", "enabled": False})

    store.refresh.assert_awaited_once()
    store.save.assert_awaited_once()
    saved = store.save.call_args[0][0]
    assert saved.enabled is False
    assert saved.trigger_id == "t-new"


def _entry(action: ActionRequest) -> list[object]:
    """Shape an XREADGROUP reply in production wire shape.

    The triggers pool is decode_responses=False, so the stream key, entry field
    names, and values all arrive as bytes — feeding str keys here would let a
    bytes-handling regression pass (the class of bug that shipped the WebAuthn
    challenge-bytes defect).
    """
    return [(b"alfred:actions", [(b"1-0", {b"event": action.model_dump_json().encode()})])]


@pytest.mark.asyncio
async def test_actions_loop_fires_trigger_engine_action() -> None:
    from core.triggers.__main__ import _shutdown, actions_loop

    trigger = _make_trigger("t-1")
    store = AsyncMock()
    store.get = AsyncMock(return_value=trigger)
    engine = AsyncMock()

    action = ActionRequest(
        source="admin-api",
        target_service="trigger-engine",
        tool_name="fire_trigger",
        parameters={"trigger_id": "t-1"},
    )

    r = AsyncMock()
    call_count = 0

    async def _xreadgroup(*_a: object, **_k: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _entry(action)
        _shutdown.set()
        return []

    r.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    r.xack = AsyncMock()
    r.xgroup_create = AsyncMock()

    _shutdown.clear()
    await actions_loop(store, engine, r)
    _shutdown.clear()

    engine.fire.assert_awaited_once()
    assert engine.fire.call_args.kwargs.get("fired_by") == "admin"
    r.xack.assert_awaited()


@pytest.mark.asyncio
async def test_actions_loop_ignores_non_trigger_engine_entries() -> None:
    """Entries for other target_services are acked but never dispatched."""
    from core.triggers.__main__ import _shutdown, actions_loop

    store = AsyncMock()
    store.get = AsyncMock(return_value=_make_trigger("t-1"))
    engine = AsyncMock()

    foreign = ActionRequest(
        source="admin-api",
        target_service="conscious-engine",
        tool_name="run_librarian",
    )

    r = AsyncMock()
    acked: list[object] = []
    call_count = 0

    async def _xreadgroup(*_a: object, **_k: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _entry(foreign)
        _shutdown.set()
        return []

    async def _xack(*args: object) -> None:
        acked.append(args[-1])

    r.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    r.xack = AsyncMock(side_effect=_xack)
    r.xgroup_create = AsyncMock()

    _shutdown.clear()
    await actions_loop(store, engine, r)
    _shutdown.clear()

    engine.fire.assert_not_awaited()
    store.save.assert_not_awaited()
    # The foreign entry was still acked (skipped, not left pending).
    assert b"1-0" in acked


@pytest.mark.asyncio
async def test_actions_loop_acks_and_survives_malformed_entry() -> None:
    """A corrupt (non-JSON) event payload is acked and skipped, not left pending,
    and does not kill the consumer loop."""
    from core.triggers.__main__ import _shutdown, actions_loop

    store = AsyncMock()
    engine = AsyncMock()

    r = AsyncMock()
    acked: list[object] = []
    call_count = 0

    async def _xreadgroup(*_a: object, **_k: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [(b"alfred:actions", [(b"9-0", {b"event": b"{not json"})])]
        _shutdown.set()
        return []

    r.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    r.xack = AsyncMock(side_effect=lambda *a: acked.append(a[-1]))
    r.xgroup_create = AsyncMock()

    _shutdown.clear()
    await actions_loop(store, engine, r)
    _shutdown.clear()

    engine.fire.assert_not_awaited()
    assert b"9-0" in acked  # malformed entry acked, not stranded in the PEL
