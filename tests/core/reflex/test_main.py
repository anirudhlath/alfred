"""Reflex's process wiring: its model, and the prompts it warms before the first decision."""

from __future__ import annotations

import dataclasses
import logging

import pytest
from pydantic_ai.models.openai import OpenAIChatModel

from core.reflex.__main__ import build_agent, warmup_steps
from core.reflex.prose import fetch_prose
from shared.config import AlfredConfig
from shared.prompts.names import REFLEX_RULES, REFLEX_STATE_CHANGE, REFLEX_TRIGGER
from shared.prompts.store import PromptStore
from tests.shared.prompts.fakes import FakeLangfuse


@pytest.fixture
def no_vllm_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """``build_agent`` exports the base URL to ``os.environ``; have it undone after the test."""
    monkeypatch.setenv("VLLM_BASE_URL", "")
    monkeypatch.delenv("VLLM_BASE_URL")
    return monkeypatch


def test_the_agent_is_built_over_the_configured_model(no_vllm_env: pytest.MonkeyPatch) -> None:
    config = dataclasses.replace(
        AlfredConfig.from_env(),
        reflex_model="vllm:test-model",
        vllm_base_url="http://vllm.test:8000/v1",
    )

    agent = build_agent(config)

    assert isinstance(agent.model, OpenAIChatModel)
    assert agent.model.model_name == "test-model"
    assert agent.model.base_url == "http://vllm.test:8000/v1/"


def test_without_a_model_reflex_logs_and_exits_non_zero(
    no_vllm_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    config = dataclasses.replace(AlfredConfig.from_env(), reflex_model="")

    with caplog.at_level(logging.ERROR), pytest.raises(SystemExit) as stopped:
        build_agent(config)

    assert stopped.value.code not in (0, None)
    assert "REFLEX_MODEL" in caplog.text


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
