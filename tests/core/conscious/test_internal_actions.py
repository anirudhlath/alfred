"""Internal-action dispatch through the production consumer (_consume_internal_actions)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest


def _entry(action: ActionRequest) -> list[object]:
    """XREADGROUP reply in production wire shape (decode_responses=False → bytes)."""
    return [(b"alfred:actions", [(b"1-0", {b"event": action.model_dump_json().encode()})])]


async def _drive_once(action: ActionRequest) -> tuple[AsyncMock, list[object]]:
    """Run one dispatch cycle of the real consumer with `run_librarian` registered."""
    from core.conscious import __main__ as cmain

    called = AsyncMock()
    cmain._INTERNAL_HANDLERS["run_librarian"] = called

    acked: list[object] = []
    call_count = 0

    async def _xreadgroup(*_a: object, **_k: object) -> list[object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _entry(action)
        cmain._shutdown.set()
        return []

    redis = AsyncMock()
    redis.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    redis.xack = AsyncMock(side_effect=lambda *a: acked.append(a[-1]))
    redis.xgroup_create = AsyncMock()

    cmain._shutdown.clear()
    try:
        await cmain._consume_internal_actions(redis, MagicMock())
    finally:
        cmain._shutdown.clear()
        cmain._INTERNAL_HANDLERS.pop("run_librarian", None)
    return called, acked


@pytest.mark.asyncio
async def test_consume_dispatches_registered_handler_and_acks() -> None:
    """A conscious-engine ActionRequest routes to the registered handler and is acked —
    exercises the real dispatch path, not a handler the test itself installs and calls."""
    action = ActionRequest(
        source="admin-api", target_service="conscious-engine", tool_name="run_librarian"
    )
    called, acked = await _drive_once(action)
    called.assert_awaited_once()
    assert b"1-0" in acked


@pytest.mark.asyncio
async def test_consume_ignores_other_target_services_but_acks() -> None:
    """Entries for another target_service are acked (skipped), never dispatched here."""
    foreign = ActionRequest(
        source="admin-api", target_service="trigger-engine", tool_name="fire_trigger"
    )
    called, acked = await _drive_once(foreign)
    called.assert_not_awaited()
    assert b"1-0" in acked
