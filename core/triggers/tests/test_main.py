"""Tests for the Trigger Engine entry point."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import StateChangedEvent
from core.triggers.registry import TriggerRegistry
from shared.streams import HOME_STATE_STREAM


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TriggerRegistry, "_registry", {})
    import core.triggers.types  # noqa: F401


def test_main_is_importable() -> None:
    import core.triggers.__main__  # noqa: F401


def test_main_function_exists() -> None:
    from core.triggers.__main__ import main

    assert callable(main)


@pytest.mark.asyncio
async def test_tick_loop_calls_evaluate(monkeypatch: pytest.MonkeyPatch) -> None:
    from core.triggers.__main__ import _shutdown, tick_loop

    mock_engine = AsyncMock()
    mock_engine.evaluate_tick = AsyncMock()

    call_count = 0

    async def fake_sleep(duration: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 1:
            _shutdown.set()

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    _shutdown.clear()
    await tick_loop(mock_engine)

    mock_engine.evaluate_tick.assert_called()
    _shutdown.clear()


class _FakeEventRedis:
    """Fake Redis serving one batch of stream entries, then signalling shutdown."""

    def __init__(self, entries: list[tuple[str, dict[str, str]]]) -> None:
        self._entries = entries
        self.read_streams: list[dict[str, str]] = []
        self.read_groups: list[str] = []
        self.acked: list[tuple[str, str, str]] = []
        self.groups_created: list[tuple[str, str]] = []

    async def xgroup_create(self, stream: str, group: str, **kwargs: Any) -> None:
        self.groups_created.append((stream, group))

    async def xreadgroup(
        self,
        group: str,
        consumer: str,
        streams: dict[str, str],
        count: int,
        block: int,
    ) -> list[tuple[str, list[tuple[str, dict[str, str]]]]]:
        from core.triggers.__main__ import _shutdown

        self.read_groups.append(group)
        self.read_streams.append(dict(streams))
        _shutdown.set()
        return [(next(iter(streams)), self._entries)]

    async def xack(self, stream: str, group: str, entry_id: str) -> None:
        self.acked.append((stream, group, entry_id))


@pytest.mark.asyncio
async def test_event_loop_consumes_home_state_stream(
    tv_on_event: StateChangedEvent,
) -> None:
    """Sensor evaluation must read real state changes from HOME_STATE_STREAM.

    Regression guard: home state changes are published to alfred:home:state_changed
    (by the MQTT bridge), NOT to alfred:events — consuming the wrong stream means
    sensor triggers never fire.
    """
    from core.triggers.__main__ import GROUP, _shutdown, event_loop

    redis = _FakeEventRedis([("1-0", {"event": tv_on_event.model_dump_json()})])
    engine = AsyncMock()

    _shutdown.clear()
    await event_loop(engine, redis)  # type: ignore[arg-type]
    _shutdown.clear()

    assert redis.groups_created == [(HOME_STATE_STREAM, GROUP)]
    assert all(HOME_STATE_STREAM in streams for streams in redis.read_streams)
    engine.evaluate_event.assert_awaited_once()
    parsed = engine.evaluate_event.await_args.args[0]
    assert isinstance(parsed, StateChangedEvent)
    assert parsed.entity_id == tv_on_event.entity_id
    assert redis.acked == [(HOME_STATE_STREAM, GROUP, "1-0")]


@pytest.mark.asyncio
async def test_event_loop_acks_and_warns_on_invalid_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed stream entries are acked, skipped, and logged (not silent)."""
    from core.triggers.__main__ import GROUP, _shutdown, event_loop

    redis = _FakeEventRedis([("1-0", {"event": "not-json"})])
    engine = AsyncMock()

    _shutdown.clear()
    with caplog.at_level("WARNING", logger="core.triggers.__main__"):
        await event_loop(engine, redis)  # type: ignore[arg-type]
    _shutdown.clear()

    engine.evaluate_event.assert_not_awaited()
    assert redis.acked == [(HOME_STATE_STREAM, GROUP, "1-0")]
    assert any("1-0" in rec.getMessage() for rec in caplog.records)
