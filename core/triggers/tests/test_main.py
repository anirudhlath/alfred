"""Tests for the Trigger Engine entry point."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.triggers.registry import TriggerRegistry


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
