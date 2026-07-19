"""Pending critical-action storage and confirmation republish."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest


def _action() -> ActionRequest:
    return ActionRequest(
        source="conscious-engine",
        target_service="home-service",
        tool_name="home.unlock_door",
        parameters={"entity_id": "lock.front_door"},
    )


@pytest.mark.asyncio
async def test_store_pending_sets_with_ttl() -> None:
    from core.routing.pending import store_pending_action

    redis = AsyncMock()
    action = _action()
    await store_pending_action(redis, action)

    redis.set.assert_awaited_once()
    args, kwargs = redis.set.call_args
    assert args[0] == f"alfred:pending_actions:{action.request_id}"
    assert kwargs["ex"] == 300
    assert ActionRequest.model_validate_json(args[1]).tool_name == "home.unlock_door"


@pytest.mark.asyncio
async def test_confirm_republishes_with_marker_and_deletes() -> None:
    from core.routing.pending import confirm_pending_action

    action = _action()
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())

    confirmed = await confirm_pending_action(redis, action.request_id)

    assert confirmed is not None
    assert confirmed.confirmed is True
    stream, fields = redis.xadd.call_args[0]
    assert stream == "alfred:actions"
    republished = ActionRequest.model_validate_json(fields["event"])
    assert republished.confirmed is True
    assert republished.request_id == action.request_id
    redis.delete.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")


@pytest.mark.asyncio
async def test_confirm_missing_returns_none() -> None:
    from core.routing.pending import confirm_pending_action

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    assert await confirm_pending_action(redis, "ghost") is None
    redis.xadd.assert_not_called()
    redis.delete.assert_not_called()
