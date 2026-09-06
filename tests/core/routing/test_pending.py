"""Pending critical-action storage and confirmation republish."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for item in items:
        yield item


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
async def test_confirm_republishes_with_marker_and_getdels() -> None:
    from core.routing.pending import confirm_pending_action

    action = _action()
    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=action.model_dump_json().encode())

    confirmed = await confirm_pending_action(redis, action.request_id)

    assert confirmed is not None
    assert confirmed.confirmed is True
    stream, fields = redis.xadd.call_args[0]
    assert stream == "alfred:actions"
    republished = ActionRequest.model_validate_json(fields["event"])
    assert republished.confirmed is True
    assert republished.request_id == action.request_id
    redis.getdel.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")


@pytest.mark.asyncio
async def test_confirm_missing_returns_none() -> None:
    from core.routing.pending import confirm_pending_action

    redis = AsyncMock()
    redis.getdel = AsyncMock(return_value=None)
    assert await confirm_pending_action(redis, "ghost") is None
    redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_confirm_is_atomic_second_concurrent_confirm_is_noop() -> None:
    """Two concurrent confirms of the same id must not both republish.

    GETDEL is atomic on the Redis side, so the second caller always sees
    None. This test pins that contract with a stateful fake standing in for
    Redis: first call returns the stored JSON (and clears it), matching what
    a real GETDEL does; the second call — simulating a second confirm that
    raced in — must find nothing and must NOT xadd again.
    """
    from core.routing.pending import confirm_pending_action

    action = _action()
    stored: bytes | None = action.model_dump_json().encode()

    async def _fake_getdel(key: str) -> bytes | None:
        nonlocal stored
        value, stored = stored, None
        return value

    redis = AsyncMock()
    redis.getdel = AsyncMock(side_effect=_fake_getdel)

    first = await confirm_pending_action(redis, action.request_id)
    second = await confirm_pending_action(redis, action.request_id)

    assert first is not None
    assert first.confirmed is True
    assert second is None
    redis.xadd.assert_awaited_once()  # only the first confirm republished — no double execution


@pytest.mark.asyncio
async def test_get_pending_returns_action_and_ttl_without_consuming() -> None:
    from core.routing.pending import get_pending_action

    redis = AsyncMock()
    action = _action()
    redis.get = AsyncMock(return_value=action.model_dump_json().encode())
    redis.ttl = AsyncMock(return_value=120)

    found = await get_pending_action(redis, action.request_id)

    assert found is not None
    got, ttl = found
    assert got.request_id == action.request_id
    assert ttl == 120
    redis.get.assert_awaited_once_with(f"alfred:pending_actions:{action.request_id}")
    redis.getdel.assert_not_called()


@pytest.mark.asyncio
async def test_get_pending_missing_returns_none_and_negative_ttl_clamps() -> None:
    from core.routing.pending import get_pending_action

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    assert await get_pending_action(redis, "ghost") is None

    action = _action()
    redis.get = AsyncMock(return_value=action.model_dump_json())
    redis.ttl = AsyncMock(return_value=-2)  # key vanished between GET and TTL
    found = await get_pending_action(redis, action.request_id)
    assert found is not None
    assert found[1] == 0


@pytest.mark.asyncio
async def test_list_pending_scans_prefix_and_sorts_oldest_first() -> None:
    from core.routing.pending import list_pending_actions

    older = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 8, 0, tzinfo=UTC)})
    newer = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 8, 1, tzinfo=UTC)})
    store = {
        f"alfred:pending_actions:{newer.request_id}": newer.model_dump_json().encode(),
        f"alfred:pending_actions:{older.request_id}": older.model_dump_json().encode(),
        "alfred:pending_actions:vanished": None,
    }
    redis = AsyncMock()
    redis.scan_iter = MagicMock(return_value=_aiter(list(store)))
    redis.get = AsyncMock(side_effect=lambda key: store[key])
    redis.ttl = AsyncMock(return_value=200)

    items = await list_pending_actions(redis)

    assert [a.request_id for a, _ in items] == [older.request_id, newer.request_id]
    assert all(ttl == 200 for _, ttl in items)
    redis.scan_iter.assert_called_once_with(match="alfred:pending_actions:*", count=100)


@pytest.mark.asyncio
async def test_list_pending_skips_unreadable_entry() -> None:
    """One corrupt value must not 500 the whole list."""
    from core.routing.pending import list_pending_actions

    good = _action()
    store = {
        "alfred:pending_actions:corrupt": b"{not json",
        f"alfred:pending_actions:{good.request_id}": good.model_dump_json().encode(),
    }
    redis = AsyncMock()
    redis.scan_iter = MagicMock(return_value=_aiter(list(store)))
    redis.get = AsyncMock(side_effect=lambda key: store[key])
    redis.ttl = AsyncMock(return_value=60)

    items = await list_pending_actions(redis)

    assert [a.request_id for a, _ in items] == [good.request_id]


@pytest.mark.asyncio
async def test_list_pending_sorts_naive_timestamp_without_raising() -> None:
    """`BaseEvent.timestamp` is a bare datetime — a naive one must not break the sort."""
    from core.routing.pending import list_pending_actions

    # Deliberately naive — no tzinfo, as a hand-written or legacy entry could carry.
    naive = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 7, 0)})
    aware = _action().model_copy(update={"timestamp": datetime(2026, 9, 4, 8, 0, tzinfo=UTC)})
    store = {
        f"alfred:pending_actions:{aware.request_id}": aware.model_dump_json().encode(),
        f"alfred:pending_actions:{naive.request_id}": naive.model_dump_json().encode(),
    }
    redis = AsyncMock()
    redis.scan_iter = MagicMock(return_value=_aiter(list(store)))
    redis.get = AsyncMock(side_effect=lambda key: store[key])
    redis.ttl = AsyncMock(return_value=60)

    items = await list_pending_actions(redis)

    assert [a.request_id for a, _ in items] == [naive.request_id, aware.request_id]


def test_pending_action_payload_shape() -> None:
    from core.routing.pending import pending_action_payload

    action = _action().model_copy(update={"reason": "The dog walker is here."})
    payload = pending_action_payload(action, 90)

    assert payload["request_id"] == action.request_id
    assert payload["tool_name"] == "home.unlock_door"
    assert payload["target_service"] == "home-service"
    assert payload["parameters"] == {"entity_id": "lock.front_door"}
    assert payload["reason"] == "The dog walker is here."
    assert payload["source"] == "conscious-engine"
    assert payload["timestamp"] == action.timestamp.isoformat()
    assert payload["ttl_seconds"] == 90
    expires = datetime.fromisoformat(payload["expires_at"])
    assert 85 <= (expires - datetime.now(UTC)).total_seconds() <= 90
