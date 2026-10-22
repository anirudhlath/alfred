"""Attention tools: the verified user's, in-process."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.conscious.tools.attention import ATTENTION_FUNCTIONS, attention_toolset
from tests.core.conscious.helpers import make_deps, make_services, tool_agent
from tests.llm_fakes import Script, tool_call

_NAMES = ["attention_add", "attention_list", "attention_remove"]


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
