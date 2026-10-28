"""Reflex's process wiring: its model, staying off without one, and the prompts it warms."""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from unittest.mock import AsyncMock, MagicMock

import fakeredis
import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from core.reflex import __main__ as reflex_main
from core.reflex.__main__ import build_agent, warmup_steps
from core.reflex.prose import fetch_prose
from shared.config import AlfredConfig
from shared.prompts.names import REFLEX_RULES, REFLEX_STATE_CHANGE, REFLEX_TRIGGER
from shared.prompts.store import PromptStore
from tests.shared.prompts.fakes import FakeLangfuse


@pytest.mark.usefixtures("restored_environ")
def test_the_agent_is_built_over_the_configured_model() -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(),
        reflex_model="vllm:test-model",
        vllm_base_url="http://vllm.test:8000/v1",
    )

    agent = build_agent(config)

    assert agent is not None
    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "test-model"
    assert agent.model.base_url == "http://vllm.test:8000/v1/"


@pytest.mark.usefixtures("restored_environ")
def test_without_a_model_there_is_no_agent_and_one_error_says_reflex_is_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    with caplog.at_level(logging.ERROR):
        agent = build_agent(config)

    assert agent is None
    off = [
        r
        for r in caplog.records
        if r.name == reflex_main.logger.name and r.levelno == logging.ERROR
    ]
    assert len(off) == 1
    message = off[0].getMessage()
    assert "Reflex is off" in message
    assert "REFLEX_MODEL" in message
    assert "OPENAI_COMPAT_MODEL" in message


@pytest.mark.usefixtures("restored_environ")
async def test_without_a_model_reflex_stays_off_and_alive_until_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exiting would stop the stack: the supervisor stops everything on a clean exit, and
    after the sixth crash. Off, Reflex touches no stream, warms nothing and calls no model."""
    redis = fakeredis.FakeAsyncRedis()
    xgroup_create = AsyncMock(side_effect=AssertionError("a consumer group while off"))
    xreadgroup = AsyncMock(side_effect=AssertionError("a stream read while off"))
    monkeypatch.setattr(redis, "xgroup_create", xgroup_create)
    monkeypatch.setattr(redis, "xreadgroup", xreadgroup)
    monkeypatch.setattr(reflex_main, "create_redis", lambda _url: redis)
    warmup = MagicMock()
    monkeypatch.setattr(reflex_main, "start_warmup", warmup)
    model_built = MagicMock()
    monkeypatch.setattr(reflex_main, "build_reflex_agent", model_built)
    monkeypatch.setattr(reflex_main, "_shutdown", asyncio.Event())
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    service = asyncio.create_task(reflex_main.serve(config))
    done, _ = await asyncio.wait({service}, timeout=0.1)

    assert not done, "Reflex returned before shutdown; the supervisor would stop the stack"

    reflex_main._handle_signal()  # what SIGTERM and SIGINT call
    await asyncio.wait_for(service, timeout=1)

    xgroup_create.assert_not_awaited()
    xreadgroup.assert_not_awaited()
    warmup.assert_not_called()
    model_built.assert_not_called()


@pytest.mark.usefixtures("restored_environ")
async def test_with_a_model_reflex_runs_its_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    loops = AsyncMock()
    monkeypatch.setattr(reflex_main, "run", loops)
    config = dataclasses.replace(
        AlfredConfig.from_env(),
        reflex_model="vllm:test-model",
        vllm_base_url="http://vllm.test:8000/v1",
    )

    await reflex_main.serve(config)

    loops.assert_awaited_once()
    assert loops.await_args is not None
    assert loops.await_args.args[0] is config


async def test_the_warmup_fetches_the_three_prompts_so_the_first_decision_pays_nothing() -> None:
    langfuse = FakeLangfuse(
        {REFLEX_STATE_CHANGE: "state change", REFLEX_TRIGGER: "trigger", REFLEX_RULES: "rules"}
    )
    store = PromptStore(langfuse=langfuse)

    await warmup_steps(store)["reflex prompts"]()

    assert sorted(name for name, _ in langfuse.calls) == sorted(
        [REFLEX_STATE_CHANGE, REFLEX_TRIGGER, REFLEX_RULES]
    )
    warm = len(langfuse.calls)

    await fetch_prose(store, "state_change")
    await fetch_prose(store, "trigger_fired")

    assert len(langfuse.calls) == warm  # both origins served from the cache
