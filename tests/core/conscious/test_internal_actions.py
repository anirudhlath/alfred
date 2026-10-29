"""Internal-action dispatch through the production consumer (_consume_internal_actions)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from bus.schemas.events import ActionRequest, ActionResult
from core.notifications.schema import Urgency
from shared.streams import ACTION_RESULTS_MAXLEN, HOME_ACTION_RESULTS_STREAM


def _entry(action: ActionRequest) -> list[object]:
    """XREADGROUP reply in production wire shape (decode_responses=False → bytes)."""
    return [(b"alfred:actions", [(b"1-0", {b"event": action.model_dump_json().encode()})])]


async def _drive_once(
    action: ActionRequest,
    router: object | None = None,
    *,
    notifier: object | None = None,
    log: object | None = None,
    redis: AsyncMock | None = None,
) -> tuple[AsyncMock, list[object]]:
    """Run one dispatch cycle of the real consumer with `run_librarian` registered; the
    notifier is a mock unless the test passes its own."""
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

    redis = redis if redis is not None else AsyncMock()
    redis.xreadgroup = AsyncMock(side_effect=_xreadgroup)
    redis.xack = AsyncMock(side_effect=lambda *a: acked.append(a[-1]))
    redis.xgroup_create = AsyncMock()

    cmain._shutdown.clear()
    try:
        await cmain._consume_internal_actions(
            redis,
            log if log is not None else MagicMock(),
            router,
            notifier if notifier is not None else AsyncMock(),
        )
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


@pytest.mark.asyncio
async def test_confirmed_domain_action_routes_through_router() -> None:
    """A confirmed=True entry for a registered domain service is executed."""
    from bus.schemas.events import ActionResult

    router = AsyncMock()
    router.registered_services = {"home-service"}
    router.route = AsyncMock(
        return_value=ActionResult(
            source="stub", request_id="r", tool_name="home.unlock_door", status="success"
        )
    )
    action = ActionRequest(
        source="domain-router",
        target_service="home-service",
        tool_name="home.unlock_door",
        confirmed=True,
    )
    _, acked = await _drive_once(action, router=router)
    router.route.assert_awaited_once()
    assert router.route.call_args[0][0].confirmed is True
    assert b"1-0" in acked


@pytest.mark.asyncio
async def test_unconfirmed_domain_action_is_skipped() -> None:
    """Unmarked domain entries keep the pre-existing ack-and-skip behavior."""
    router = AsyncMock()
    router.registered_services = {"home-service"}
    action = ActionRequest(
        source="trigger-engine", target_service="home-service", tool_name="home.turn_on_lights"
    )
    _, acked = await _drive_once(action, router=router)
    router.route.assert_not_awaited()
    assert b"1-0" in acked


def _confirmed(tool: str = "home.unlock_door") -> ActionRequest:
    return ActionRequest(
        source="domain-router",
        target_service="home-service",
        tool_name=tool,
        confirmed=True,
    )


def _reflex_confirmed(tool: str = "home.light_turn_on") -> ActionRequest:
    """An action Reflex proposed and the owner said yes to."""
    return ActionRequest(
        source="reflex-engine",
        target_service="home-service",
        tool_name=tool,
        confirmed=True,
    )


def _router_returning(result: ActionResult) -> AsyncMock:
    router = AsyncMock()
    router.registered_services = {"home-service"}
    router.route = AsyncMock(return_value=result)
    return router


def _outcome(action: ActionRequest, status: str, error: str | None = None) -> ActionResult:
    return ActionResult(
        source="home-agent",
        request_id=action.request_id,
        tool_name=action.tool_name,
        status=status,  # type: ignore[arg-type]
        error=error,
    )


@pytest.mark.asyncio
async def test_a_confirmed_action_publishes_its_result() -> None:
    """The PWA's "applied" state waits on the action's request id on this stream."""
    action = _confirmed()
    result = _outcome(action, "success")
    redis = AsyncMock()

    await _drive_once(action, _router_returning(result), notifier=AsyncMock(), redis=redis)

    (call,) = redis.xadd.await_args_list
    stream, fields = call.args
    assert stream == HOME_ACTION_RESULTS_STREAM
    assert call.kwargs == {"maxlen": ACTION_RESULTS_MAXLEN, "approximate": True}
    published = ActionResult.model_validate_json(fields["event"])
    assert published.request_id == action.request_id
    assert published.status == "success"


@pytest.mark.asyncio
async def test_a_succeeded_confirmed_action_notifies() -> None:
    action = _confirmed()
    notifier = AsyncMock()

    await _drive_once(action, _router_returning(_outcome(action, "success")), notifier=notifier)

    notifier.publish.assert_awaited_once_with(
        title="Done",
        body="I ran 'home.unlock_door' on home-service, sir.",
        source="conscious-engine",
        urgency=Urgency.IMPORTANT,
    )


@pytest.mark.asyncio
async def test_a_failed_confirmed_action_warns_and_notifies() -> None:
    action = _confirmed()
    notifier = AsyncMock()
    log = MagicMock()
    result = _outcome(action, "error", "lock jammed")

    await _drive_once(action, _router_returning(result), notifier=notifier, log=log)

    log.warning.assert_called_once()
    assert "lock jammed" in str(log.warning.call_args)
    notifier.publish.assert_awaited_once_with(
        title="That didn't work",
        body="'home.unlock_door' on home-service failed: lock jammed",
        source="conscious-engine",
        urgency=Urgency.IMPORTANT,
    )


@pytest.mark.asyncio
async def test_a_confirmed_action_that_raises_is_reported_as_a_failure() -> None:
    action = _confirmed()
    router = AsyncMock()
    router.registered_services = {"home-service"}
    router.route = AsyncMock(side_effect=RuntimeError("agent exploded"))
    notifier = AsyncMock()
    redis = AsyncMock()
    log = MagicMock()

    _, acked = await _drive_once(action, router, notifier=notifier, log=log, redis=redis)

    assert b"1-0" in acked
    assert notifier.publish.await_args.kwargs["title"] == "That didn't work"
    assert "agent exploded" in notifier.publish.await_args.kwargs["body"]
    published = ActionResult.model_validate_json(redis.xadd.await_args.args[1]["event"])
    assert (published.request_id, published.status) == (action.request_id, "error")


@pytest.mark.asyncio
async def test_a_failing_notifier_does_not_stop_the_ack_or_the_result() -> None:
    action = _confirmed()
    notifier = AsyncMock()
    notifier.publish = AsyncMock(side_effect=ConnectionError("dispatcher down"))
    redis = AsyncMock()

    _, acked = await _drive_once(
        action, _router_returning(_outcome(action, "success")), notifier=notifier, redis=redis
    )

    assert b"1-0" in acked
    redis.xadd.assert_awaited_once()


@pytest.mark.asyncio
async def test_an_unconfirmed_or_foreign_action_publishes_no_result() -> None:
    notifier = AsyncMock()
    redis = AsyncMock()
    unconfirmed = ActionRequest(
        source="trigger-engine", target_service="home-service", tool_name="home.turn_on_lights"
    )

    await _drive_once(
        unconfirmed,
        _router_returning(_outcome(unconfirmed, "success")),
        notifier=notifier,
        redis=redis,
    )

    redis.xadd.assert_not_awaited()
    notifier.publish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_reflex_action_that_worked_is_not_announced() -> None:
    """The owner has just said yes: "Done" would only repeat it. The result still goes out."""
    action = _reflex_confirmed()
    notifier = AsyncMock()
    redis = AsyncMock()
    log = MagicMock()

    _, acked = await _drive_once(
        action,
        _router_returning(_outcome(action, "success")),
        notifier=notifier,
        redis=redis,
        log=log,
    )

    notifier.publish.assert_not_awaited()
    (call,) = redis.xadd.await_args_list
    assert ActionResult.model_validate_json(call.args[1]["event"]).status == "success"
    log.info.assert_called()
    assert b"1-0" in acked


@pytest.mark.asyncio
async def test_a_reflex_action_that_failed_is_still_reported() -> None:
    action = _reflex_confirmed()
    notifier = AsyncMock()
    log = MagicMock()

    await _drive_once(
        action,
        _router_returning(_outcome(action, "error", "lamp unreachable")),
        notifier=notifier,
        log=log,
    )

    log.warning.assert_called_once()
    notifier.publish.assert_awaited_once_with(
        title="That didn't work",
        body="'home.light_turn_on' on home-service failed: lamp unreachable",
        source="conscious-engine",
        urgency=Urgency.IMPORTANT,
    )


@pytest.mark.asyncio
async def test_a_reflex_action_that_raises_is_reported_as_a_failure() -> None:
    action = _reflex_confirmed()
    router = AsyncMock()
    router.registered_services = {"home-service"}
    router.route = AsyncMock(side_effect=RuntimeError("agent exploded"))
    notifier = AsyncMock()

    await _drive_once(action, router, notifier=notifier)

    assert notifier.publish.await_args.kwargs["title"] == "That didn't work"
