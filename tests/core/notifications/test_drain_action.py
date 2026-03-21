"""Tests for the drain-deferred action handler."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from core.notifications.drain_action import handle_drain_deferred


@pytest.mark.asyncio
async def test_handle_drain_deferred_calls_dispatcher() -> None:
    dispatcher = AsyncMock()
    await handle_drain_deferred(dispatcher)
    dispatcher.drain_deferred.assert_called_once()
