"""Registry tools: offered by identity, validated by their own schema, routed as actions."""

from __future__ import annotations

import datetime as _dt  # noqa: TC003 — the SDK and Pydantic resolve annotations at runtime
import enum
import json
import logging
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic import BaseModel
from pydantic_ai import DeferredToolRequests, DeferredToolResults, ToolApproved, ToolFailed

from bus.schemas.events import ActionResult
from core.conscious.tools import names, validation
from core.conscious.tools.registry import RegistryToolset
from core.reflex.tool_registry import REFLEX_AUDIENCE, ToolInfo, ToolRegistry
from core.routing.domain_router import CONFIRMATION_REQUIRED_PREFIX
from core.triggers.models import TRIGGER_ENGINE_SOURCE
from sdk.alfred_sdk.client import AlfredClient
from sdk.alfred_sdk.feature import BaseFeature, tool
from shared.redis_streams import forward_range
from shared.streams import HOME_ACTION_RESULTS_STREAM
from tests.core.conscious.helpers import (
    call_direct,
    make_deps,
    make_services,
    run_context,
    tool_agent,
)
from tests.llm_fakes import Script, tool_call

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _fresh_logs(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(names, "_reported", set())
    validation._compile.cache_clear()
    yield
    validation._compile.cache_clear()


def _info(
    name: str, risk: str = "benign", audience: str = REFLEX_AUDIENCE, **overrides: Any
) -> ToolInfo:
    fields: dict[str, Any] = {
        "name": name,
        "description": f"{name} does a thing",
        "feature_name": name.split(".")[0],
        "feature_description": "Home control",
        "target_service": "home-service",
        "audience": audience,
        "risk": risk,
        "input_schema": {
            "type": "object",
            "properties": {"entity_id": {"type": "string", "description": "Entity"}},
            "required": ["entity_id"],
        },
    }
    fields.update(overrides)
    return ToolInfo(**fields)


def _router(result: Any = None, *, error: str | None = None) -> AsyncMock:
    router = AsyncMock()
    if error is None:
        router.route.return_value = MagicMock(status="success", result=result, error=None)
    else:
        router.route.return_value = MagicMock(status="error", result=None, error=error)
    return router


async def _run(
    script: Script,
    tools: list[ToolInfo],
    *,
    who: str = "sir",
    router: Any = None,
    redis: Any = None,
):
    services: dict[str, Any] = {"router": router or _router({"ok": True})}
    if redis is not None:
        services["redis"] = redis
    deps = make_deps(
        who,  # type: ignore[arg-type]
        registry_tools=tools,
        services=make_services(**services),
    )
    result = await tool_agent(script, RegistryToolset()).run("go", deps=deps)
    return result, deps


APPROVAL_ID = "appr-1"


async def _approved(script: Script, tools: list[ToolInfo], *, router: Any, redis: Any = None):
    """Run until the critical calls defer, then resume with all of them approved, as the
    engine does once the owner says yes; the resumed run carries ``APPROVAL_ID``."""
    result, deps = await _run(script, tools, router=router, redis=redis)
    deferred = result.output
    assert isinstance(deferred, DeferredToolRequests)
    deps.approval_id = APPROVAL_ID
    approvals = {part.tool_call_id: ToolApproved() for part in deferred.approvals}
    resumed = await tool_agent(script, RegistryToolset()).run(
        message_history=result.all_messages(),
        deferred_tool_results=DeferredToolResults(approvals=approvals),
        deps=deps,
    )
    return deferred, resumed, deps


async def _published(redis: Any) -> list[ActionResult]:
    return [
        ActionResult.model_validate_json(fields[b"event"])
        for _id, fields in await forward_range(redis, HOME_ACTION_RESULTS_STREAM)
    ]


@pytest.mark.asyncio
async def test_a_call_routes_one_action_request_under_the_registry_name() -> None:
    router = _router({"state": "on"})
    script = Script(tool_call("home_light_on", {"entity_id": "light.desk"}), "done")

    result, deps = await _run(script, [_info("home.light_on")], router=router)

    assert result.output == "done"
    action = router.route.await_args.args[0]
    assert (action.source, action.target_service, action.tool_name) == (
        "conscious-engine",
        "home-service",
        "home.light_on",
    )
    assert action.parameters == {"entity_id": "light.desk"}
    assert action.reason is None and action.confirmed is False
    assert deps.actions_taken == ["home.light_on"]
    assert json.loads(script.returns(1)[0]) == {"state": "on"}


@pytest.mark.asyncio
async def test_a_success_without_a_result_reads_as_success() -> None:
    script = Script(tool_call("home_light_on", {"entity_id": "light.desk"}), "done")
    await _run(script, [_info("home.light_on")], router=_router(None))
    assert json.loads(script.returns(1)[0]) == {"status": "success"}


@pytest.mark.asyncio
async def test_domain_error_reaches_the_model_without_spending_a_retry() -> None:
    call = tool_call("home_light_on", {"entity_id": "light.desk"})
    # Three failures in a row would exhaust two retries if a failure spent one.
    script = Script(call, call, call, "It is offline, sir.")

    result, deps = await _run(
        script, [_info("home.light_on")], router=_router(error="Device offline")
    )

    assert result.output == "It is offline, sir."
    assert script.returns(1) == ["Device offline"]
    assert script.outcomes(1) == ["failed"]
    assert script.retries(1) == []
    assert deps.actions_taken == ["home.light_on"] * 3


# ── the approval gate (#317 PR 5): a critical call waits for the owner ─────────


@pytest.mark.asyncio
async def test_a_critical_call_defers_instead_of_running() -> None:
    router = _router({"ok": True})
    args = {"entity_id": "lock.front", "reason": "The dog walker is here."}
    script = Script(tool_call("home_unlock_door", args))

    result, deps = await _run(script, [_info("home.unlock_door", risk="critical")], router=router)

    deferred = result.output
    assert isinstance(deferred, DeferredToolRequests)
    [part] = deferred.approvals
    assert part.tool_name == "home_unlock_door"
    assert deferred.metadata[part.tool_call_id] == {
        "tool_name": "home.unlock_door",
        "target_service": "home-service",
        "parameters": {"entity_id": "lock.front"},
        "reason": "The dog walker is here.",
    }
    router.route.assert_not_awaited()
    assert deps.actions_taken == []
    assert script.call_count == 1


@pytest.mark.asyncio
async def test_bad_arguments_retry_before_the_gate() -> None:
    router = _router({"ok": True})
    script = Script(tool_call("home_unlock_door", {"entity": "lock.front"}), "gave up")

    result, _ = await _run(script, [_info("home.unlock_door", risk="critical")], router=router)

    assert result.output == "gave up"
    assert "'entity_id' is a required property" in script.retries(1)[0]
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_approved_call_runs_confirmed_and_publishes_its_result() -> None:
    redis = fakeredis.FakeAsyncRedis()
    router = _router({"locked": False})
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "Unlocked, sir.")

    _, resumed, deps = await _approved(
        script, [_info("home.unlock_door", risk="critical")], router=router, redis=redis
    )

    assert resumed.output == "Unlocked, sir."
    router.route.assert_awaited_once()
    action = router.route.await_args.args[0]
    assert action.confirmed is True
    assert action.parameters == {"entity_id": "lock.front"}
    assert json.loads(script.returns(1)[0]) == {"locked": False}
    [published] = await _published(redis)
    assert (published.request_id, published.tool_name, published.status) == (
        APPROVAL_ID,
        "home.unlock_door",
        "success",
    )
    assert published.result == {"locked": False}
    assert deps.actions_taken == ["home.unlock_door"]


@pytest.mark.asyncio
async def test_an_approved_failure_reaches_the_model_and_the_door() -> None:
    redis = fakeredis.FakeAsyncRedis()
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "It jammed, sir.")

    await _approved(
        script,
        [_info("home.unlock_door", risk="critical")],
        router=_router(error="Lock jammed"),
        redis=redis,
    )

    assert script.returns(1) == ["Lock jammed"]
    assert script.outcomes(1) == ["failed"]
    [published] = await _published(redis)
    assert (published.request_id, published.status, published.error) == (
        APPROVAL_ID,
        "error",
        "Lock jammed",
    )


@pytest.mark.asyncio
async def test_an_approved_call_whose_route_raises_still_reaches_the_door() -> None:
    """The Door waits on the approval's result: a route that raises must not leave it
    waiting until it times out. The model still learns only that the call failed."""
    redis = fakeredis.FakeAsyncRedis()
    router = AsyncMock()
    router.route.side_effect = ConnectionError("secret transport detail")
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "It failed, sir.")

    await _approved(
        script, [_info("home.unlock_door", risk="critical")], router=router, redis=redis
    )

    assert script.outcomes(1) == ["failed"]
    assert script.returns(1) == ["home_unlock_door failed"]
    [published] = await _published(redis)
    assert (published.request_id, published.tool_name, published.status) == (
        APPROVAL_ID,
        "home.unlock_door",
        "error",
    )
    assert published.error and "secret transport detail" not in published.error


@pytest.mark.asyncio
async def test_a_lost_result_publish_still_returns_the_call() -> None:
    """The action ran: a Door that misses its result must not turn it into a failure."""
    redis = fakeredis.FakeAsyncRedis()
    redis.xadd = AsyncMock(side_effect=ConnectionError("redis went away"))  # type: ignore[method-assign]
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "Unlocked, sir.")

    await _approved(
        script,
        [_info("home.unlock_door", risk="critical")],
        router=_router({"locked": False}),
        redis=redis,
    )

    assert script.outcomes(1) == ["success"]
    assert json.loads(script.returns(1)[0]) == {"locked": False}


@pytest.mark.asyncio
async def test_an_unapproved_call_publishes_no_result() -> None:
    redis = fakeredis.FakeAsyncRedis()
    script = Script(tool_call("home_light_on", {"entity_id": "light.desk"}), "done")

    await _run(script, [_info("home.light_on")], redis=redis)

    assert await _published(redis) == []


@pytest.mark.asyncio
async def test_a_router_intercept_carries_no_id() -> None:
    """The router's own critical check (defence in depth) parks the action under an id
    the model must never see: it could pass it to something that approves."""
    router = _router(error=f"{CONFIRMATION_REQUIRED_PREFIX}req-42")
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "Asked, sir.")

    _, deps = await _run(script, [_info("home.unlock_door", risk="elevated")], router=router)

    assert script.returns(1) == [
        "This action needs the owner's confirmation; a request was sent to their phone."
    ]
    assert script.outcomes(1) == ["failed"]
    assert "req-42" not in script.returns(1)[0]
    assert router.route.await_args.args[0].confirmed is False
    assert deps.actions_taken == ["home.unlock_door"]


@pytest.mark.asyncio
async def test_guest_calling_a_benign_tool_is_routed_and_recorded() -> None:
    router = _router({"lights": []})
    script = Script(tool_call("home_get_lights", {"entity_id": "light.desk"}), "here")

    _, deps = await _run(script, [_info("home.get_lights")], who="guest", router=router)

    router.route.assert_awaited_once()
    assert router.route.await_args.args[0].tool_name == "home.get_lights"
    assert script.outcomes(1) == ["success"]
    assert deps.actions_taken == ["home.get_lights"]


@pytest.mark.asyncio
async def test_invalid_arguments_are_retried_with_the_schema_errors_and_never_routed() -> None:
    router = _router({"ok": True})
    script = Script(
        tool_call("home_light_on", {"entity": "light.desk"}),
        tool_call("home_light_on", "[1, 2]"),
        "gave up",
    )

    _, deps = await _run(script, [_info("home.light_on")], router=router)

    assert "'entity_id' is a required property" in script.retries(1)[0]
    assert script.retries(2)
    router.route.assert_not_awaited()
    assert deps.actions_taken == []


@pytest.mark.asyncio
async def test_reflex_audience_tools_load_and_others_are_deferred() -> None:
    tools = [
        _info("home.light_turn_on"),
        _info("home.vacuum_start", audience="conscious"),
        _info("home.call_service", "critical", audience="conscious"),
    ]
    deps = make_deps(registry_tools=tools)

    offered = await RegistryToolset().get_tools(run_context(deps))

    assert {name: tool.tool_def.defer_loading for name, tool in offered.items()} == {
        "home_light_turn_on": False,
        "home_vacuum_start": True,
        "home_call_service": True,
    }


@pytest.mark.asyncio
async def test_guest_is_offered_only_benign_registry_tools() -> None:
    tools = [
        _info("home.get_lights"),
        _info("home.light_on", risk="elevated"),
        _info("home.unlock_door", risk="critical"),
    ]
    guest, sir = Script("hi"), Script("hi")

    await _run(guest, tools, who="guest")
    await _run(sir, tools, who="sir")

    assert guest.offered() == ["home_get_lights"]
    assert sir.offered() == ["home_get_lights", "home_light_on", "home_unlock_door"]


@pytest.mark.asyncio
async def test_guest_naming_an_elevated_tool_is_never_routed() -> None:
    router = _router({"ok": True})
    script = Script(tool_call("home_unlock_door", {"entity_id": "lock.front"}), "no")

    _, deps = await _run(
        script, [_info("home.unlock_door", risk="critical")], who="guest", router=router
    )

    assert "home_unlock_door" in script.retries(1)[0]
    router.route.assert_not_awaited()
    assert deps.actions_taken == []


@pytest.mark.asyncio
async def test_a_guest_reaching_call_time_is_refused_without_routing() -> None:
    router = _router({"ok": True})
    services = make_services(router=router)
    tools = [_info("home.unlock_door", risk="critical")]

    with pytest.raises(ToolFailed, match="home_unlock_door is only available to the verified user"):
        await call_direct(
            RegistryToolset(),
            "home_unlock_door",
            {"entity_id": "lock.front"},
            offered_to=make_deps("sir", registry_tools=tools, services=services),
            called_by=make_deps("guest", registry_tools=tools, services=services),
        )
    router.route.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_engine_registrations_are_left_to_the_trigger_toolset() -> None:
    script = Script("hi")
    tools = [_info("triggers.list_triggers", target_service=TRIGGER_ENGINE_SOURCE)]
    await _run(script, tools)
    assert script.offered() == []


@pytest.mark.asyncio
async def test_critical_tools_get_an_optional_reason_that_rides_the_action() -> None:
    info = _info("home.unlock_door", risk="critical")
    router = _router({"ok": True})
    script = Script(
        tool_call(
            "home_unlock_door",
            {"entity_id": "lock.front", "reason": "The dog walker is here."},
        ),
        "done",
    )

    deferred, _, _ = await _approved(script, [info, _info("home.get_lights")], router=router)

    [part] = deferred.approvals
    assert deferred.metadata[part.tool_call_id]["reason"] == "The dog walker is here."
    schema = script.schema("home_unlock_door")
    assert schema["properties"]["reason"]["type"] == "string"
    assert schema["required"] == ["entity_id"]
    assert "reason" not in script.schema("home_get_lights")["properties"]
    assert "reason" not in info.input_schema["properties"]
    action = router.route.await_args.args[0]
    assert action.reason == "The dog walker is here."
    assert action.parameters == {"entity_id": "lock.front"}


@pytest.mark.asyncio
async def test_reason_is_offered_on_a_schema_without_properties() -> None:
    info = _info("home.sound_alarm", risk="critical", input_schema={"type": "object"})
    script = Script("ok")
    await _run(script, [info])
    assert script.schema("home_sound_alarm")["properties"]["reason"]["type"] == "string"
    assert info.input_schema == {"type": "object"}


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_reason", [{"why": "nested"}, "   "])
async def test_a_non_string_or_blank_reason_is_dropped(bad_reason: object) -> None:
    router = _router({"ok": True})
    script = Script(
        tool_call("home_unlock_door", {"entity_id": "lock.front", "reason": bad_reason}), "done"
    )
    deferred, _, _ = await _approved(
        script, [_info("home.unlock_door", risk="critical")], router=router
    )
    [part] = deferred.approvals
    assert deferred.metadata[part.tool_call_id]["reason"] is None
    action = router.route.await_args.args[0]
    assert action.reason is None
    assert action.parameters == {"entity_id": "lock.front"}


@pytest.mark.asyncio
@pytest.mark.parametrize("risk", ["critical", "benign"])
async def test_a_tool_that_declares_its_own_reason_keeps_it(risk: str) -> None:
    own = {
        "type": "object",
        "properties": {"reason": {"type": "string", "description": "Incident reason"}},
        "required": ["reason"],
    }
    router = _router({"ok": True})
    script = Script(tool_call("home_log_incident", {"reason": "Smoke alarm tripped."}), "done")
    tools = [_info("home.log_incident", risk=risk, input_schema=own)]

    if risk == "critical":
        deferred, _, _ = await _approved(script, tools, router=router)
        [part] = deferred.approvals
        assert deferred.metadata[part.tool_call_id]["parameters"] == {
            "reason": "Smoke alarm tripped."
        }
        assert deferred.metadata[part.tool_call_id]["reason"] is None
    else:
        await _run(script, tools, router=router)

    assert script.schema("home_log_incident") == own
    action = router.route.await_args.args[0]
    assert action.parameters == {"reason": "Smoke alarm tripped."}
    assert action.reason is None


@pytest.mark.asyncio
async def test_uncompilable_schema_drops_only_that_tool_and_logs_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bad = _info("home.bad", input_schema={"type": "object", "properties": {"x": {"type": 7}}})
    tools = [bad, _info("home.get_lights")]

    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            script = Script("hi")
            await _run(script, tools)
            assert script.offered() == ["home_get_lights"]

    assert caplog.text.count("Tool home.bad has an invalid input_schema") == 1


@pytest.mark.asyncio
async def test_a_name_too_long_to_offer_is_dropped_and_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_name = "home." + "x" * 70
    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        for _ in range(2):
            script = Script("hi")
            await _run(script, [_info(long_name), _info("home.get_lights")])
            assert script.offered() == ["home_get_lights"]
    assert caplog.text.count("the limit is 64") == 1


@pytest.mark.asyncio
async def test_two_registry_names_with_one_wire_name_offer_the_first(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = _info("home.light_on", description="first")
    second = _info("home_light.on", description="second")
    script = Script("hi")

    with caplog.at_level(logging.ERROR, logger="core.conscious.tools.names"):
        await _run(script, [first, second])

    assert script.offered() == ["home_light_on"]
    assert script.requests[0][1].function_tools[0].description == "first"
    assert "'home.light_on' and 'home_light.on' both map to wire name 'home_light_on'" in (
        caplog.text
    )


# ── the SDK's schemas reach the model unchanged (#300, #301), ported from test_engine.py


class _Mode(enum.Enum):
    HEAT = "heat"
    COOL = "cool"


class _Window(BaseModel):
    start: _dt.datetime
    end: _dt.datetime


class _ClimateFeature(BaseFeature):
    """Climate controls."""

    feature_name = "climate"

    # Reflex audience, so the tool is loaded: these tests call it without searching.
    @tool(risk="critical", audience="reflex")
    async def set_schedule(
        self,
        zone: str,
        days: list[Literal["mon", "tue"]],
        at: _dt.datetime,
        mode: _Mode,
        window: _Window | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Schedule heating.

        Args:
            zone: Which zone.
            days: Days to run.
            at: When to start.
        """
        return {}


async def _through_the_registry(features: list[type[BaseFeature]]) -> list[ToolInfo]:
    """The real path: SDK manifest → registry JSON → ToolRegistry."""
    client = AlfredClient(service_name="svc", service_endpoint="http://svc/mcp")
    client.discover_features_from_classes(features, None)
    redis = AsyncMock()
    redis.hgetall.return_value = {b"svc": json.dumps(client.get_registration_manifest()).encode()}
    return await ToolRegistry(redis).get_tools()


@pytest.mark.asyncio
async def test_rich_sdk_types_reach_the_model() -> None:
    script = Script("ok")
    await _run(script, await _through_the_registry([_ClimateFeature]))

    schema = script.schema("climate_set_schedule")
    props = schema["properties"]
    assert schema["required"] == ["zone", "days", "at", "mode"]
    assert props["days"] == {
        "type": "array",
        "items": {"type": "string", "enum": ["mon", "tue"]},
        "description": "Days to run.",
    }
    assert props["at"] == {"type": "string", "format": "date-time", "description": "When to start."}
    assert props["mode"] == {"$ref": "#/$defs/_Mode"}
    assert schema["$defs"]["_Mode"] == {"type": "string", "enum": ["heat", "cool"]}
    assert props["window"]["anyOf"][0] == {"$ref": "#/$defs/_Window"}
    assert props["reason"]["type"] == "string"
    assert "reason" not in schema["required"]


@pytest.mark.asyncio
async def test_an_enum_outside_the_schema_is_retried() -> None:
    router = _router({"ok": True})
    bad = {"zone": "up", "days": ["sun"], "at": "2026-10-13T07:00:00Z", "mode": "heat"}
    script = Script(tool_call("climate_set_schedule", bad), "done")

    await _run(script, await _through_the_registry([_ClimateFeature]), router=router)

    assert "days.0: 'sun' is not one of ['mon', 'tue']" in script.retries(1)[0]
    router.route.assert_not_awaited()
