"""Confirmation and attention tools: the verified user's, in-process."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from bus.schemas.events import ActionRequest
from core.conscious.tools.attention import ATTENTION_FUNCTIONS, attention_toolset
from tests.core.conscious.helpers import make_deps, make_services, tool_agent
from tests.llm_fakes import Script, tool_call

_NAMES = ["attention_add", "attention_list", "attention_remove", "confirm_pending_action"]


def _redis() -> AsyncMock:
    redis = AsyncMock()
    redis.smembers = AsyncMock(return_value={b"sensor.dryer_power"})
    return redis


def test_the_functions_are_named_as_before() -> None:
    assert sorted(ATTENTION_FUNCTIONS.tools) == _NAMES


@pytest.mark.asyncio
async def test_sir_is_offered_them_and_a_guest_is_not() -> None:
    sir, guest = Script("hi"), Script("hi")
    await tool_agent(sir, attention_toolset()).run("go", deps=make_deps("sir"))
    await tool_agent(guest, attention_toolset()).run("go", deps=make_deps("guest"))
    assert sorted(sir.offered()) == _NAMES
    assert guest.offered() == []


@pytest.mark.asyncio
async def test_confirm_republishes_the_pending_action() -> None:
    action = ActionRequest(
        source="conscious-engine", target_service="home-service", tool_name="home.unlock_door"
    )
    redis = _redis()
    redis.getdel = AsyncMock(return_value=action.model_dump_json().encode())
    script = Script(tool_call("confirm_pending_action", {"request_id": action.request_id}), "ok")

    await tool_agent(script, attention_toolset()).run(
        "go", deps=make_deps(services=make_services(redis=redis))
    )

    assert json.loads(script.returns(1)[0]) == {
        "status": "confirmed",
        "request_id": action.request_id,
        "tool_name": "home.unlock_door",
    }
    stream, fields = redis.xadd.call_args[0]
    assert stream == "alfred:actions"
    assert ActionRequest.model_validate_json(fields["event"]).confirmed is True


@pytest.mark.asyncio
async def test_an_expired_confirmation_is_a_failed_result() -> None:
    redis = _redis()
    redis.getdel = AsyncMock(return_value=None)
    script = Script(tool_call("confirm_pending_action", {"request_id": "x"}), "expired")

    await tool_agent(script, attention_toolset()).run(
        "go", deps=make_deps(services=make_services(redis=redis))
    )

    assert script.returns(1) == ["No pending action 'x' — it may have expired"]


@pytest.mark.asyncio
async def test_attention_round_trip() -> None:
    redis = _redis()
    entity = {"domain": "home", "entity_id": "sensor.dryer_power"}
    script = Script(
        tool_call("attention_add", entity),
        tool_call("attention_list", {"domain": "home"}),
        tool_call("attention_remove", entity),
        "done",
    )
    deps = make_deps(services=make_services(redis=redis))

    await tool_agent(script, attention_toolset()).run("go", deps=deps)

    assert json.loads(script.returns(1)[0]) == {
        "status": "added",
        "entity_id": "sensor.dryer_power",
    }
    assert json.loads(script.returns(2)[0]) == {"entities": ["sensor.dryer_power"], "count": 1}
    assert json.loads(script.returns(3)[0]) == {
        "status": "removed",
        "entity_id": "sensor.dryer_power",
    }
    redis.sadd.assert_any_call("alfred:attention:home", "sensor.dryer_power")
    redis.srem.assert_called_once_with("alfred:attention:home", "sensor.dryer_power")
    assert deps.actions_taken == ["attention_add", "attention_list", "attention_remove"]
