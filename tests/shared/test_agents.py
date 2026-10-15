"""What every agent shares: model resolution, the role header, instrumentation."""

from __future__ import annotations

import importlib.util
import logging
import os
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

from shared.agents import instrumentation, model_key, resolve_model, role_headers
from shared.config import AlfredConfig, apply_model_env
from shared.llm_http import RetryingTransport
from shared.llm_roles import LIBRARIAN, LLM_ROLES, ROLE_HEADER, SYSTEM1, SYSTEM2
from tests.llm_fakes import Script


def test_conftest_blocks_real_model_requests() -> None:
    from pydantic_ai import models

    assert models.ALLOW_MODEL_REQUESTS is False


def test_roles_are_the_three_the_proxy_knows() -> None:
    assert ROLE_HEADER == "X-Alfred-Role"
    assert {SYSTEM1, SYSTEM2, LIBRARIAN} == LLM_ROLES == {"system1", "system2", "librarian"}
    assert role_headers(SYSTEM2) == {"X-Alfred-Role": "system2"}


def test_blank_setting_logs_and_yields_no_model(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="shared.agents"):
        assert resolve_model("SYSTEM2_MODEL", "  ") is None
    assert "SYSTEM2_MODEL is not set" in caplog.text


def test_unusable_model_string_logs_and_yields_no_model(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="shared.agents"):
        assert resolve_model("LIBRARIAN_MODEL", "nope:x") is None
    assert "LIBRARIAN_MODEL='nope:x' cannot be used" in caplog.text


def test_a_model_string_resolves_and_keys_by_provider_and_name() -> None:
    model = resolve_model("SYSTEM2_MODEL", " test ")
    assert model is not None
    assert model_key(model) == "test:test"


@pytest.mark.asyncio
async def test_instrumentation_records_the_agent_and_model_spans() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch("shared.agents.trace.get_tracer_provider", return_value=provider):
        capability = instrumentation()
    agent = Agent(Script("hello").model, name="probe", capabilities=[capability])

    await agent.run("hi")

    names = [span.name for span in exporter.get_finished_spans()]
    assert "invoke_agent probe" in names
    assert any(name.startswith("chat ") for name in names)


_PROVIDER_ENV = ("VLLM_BASE_URL", "OPENROUTER_API_KEY", "CLAUDE_API_KEY", "SYSTEM2_MODEL")


def _clear_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset the provider variables so that ``monkeypatch.undo`` restores them exactly.

    ``delenv(name, raising=False)`` records no undo for a variable that is absent, so
    anything a test then writes (``apply_model_env`` sets ``VLLM_BASE_URL``) would leak
    into later tests. ``setenv`` first records the original state, absent or present.
    """
    for name in _PROVIDER_ENV:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.fixture
def no_provider_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A developer's .env reaches os.environ on import (``shared.config``); clear it."""
    _clear_provider_env(monkeypatch)
    return monkeypatch


def test_clearing_provider_env_restores_what_a_test_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VLLM_BASE_URL", raising=False)  # baseline: absent
    inner = pytest.MonkeyPatch()
    try:
        _clear_provider_env(inner)
        os.environ["VLLM_BASE_URL"] = "http://written-by-a-test"
        inner.undo()
        assert "VLLM_BASE_URL" not in os.environ
    finally:
        os.environ.pop("VLLM_BASE_URL", None)


def test_litellm_is_gone() -> None:
    assert importlib.util.find_spec("litellm") is None


def test_a_vllm_string_resolves_against_vllm_base_url(no_provider_env: pytest.MonkeyPatch) -> None:
    no_provider_env.setenv("VLLM_BASE_URL", "http://vllm.test:8000/v1")
    model = resolve_model("SYSTEM2_MODEL", "vllm:gemma-x")
    assert model is not None
    assert model_key(model) == "vllm:gemma-x"
    assert model.base_url == "http://vllm.test:8000/v1/"


def test_an_openrouter_string_resolves_with_its_key(no_provider_env: pytest.MonkeyPatch) -> None:
    no_provider_env.setenv("OPENROUTER_API_KEY", "not-a-key")
    model = resolve_model("SYSTEM2_MODEL", "openrouter:anthropic/claude-sonnet-4")
    assert model is not None
    assert model_key(model) == "openrouter:anthropic/claude-sonnet-4"


@pytest.mark.parametrize(
    ("env", "value"),
    [
        ("VLLM_BASE_URL", "vllm:gemma-x"),
        ("OPENROUTER_API_KEY", "openrouter:vendor/model"),
        ("OPENAI_API_KEY", "openai:model-x"),
    ],
)
def test_an_openai_compatible_model_retries_in_the_transport_not_the_sdk(
    no_provider_env: pytest.MonkeyPatch, env: str, value: str
) -> None:
    no_provider_env.setenv(env, "http://vllm.test:8000/v1" if env == "VLLM_BASE_URL" else "k")
    model = resolve_model("SYSTEM2_MODEL", value)
    # `openai:` resolves to the Responses API model; both carry the same AsyncOpenAI client.
    assert isinstance(model, OpenAIChatModel | OpenAIResponsesModel)
    assert model.client.max_retries == 0
    assert isinstance(model.client._client._transport, RetryingTransport)


@pytest.mark.parametrize(
    ("value", "missing"),
    [("vllm:gemma-x", "VLLM_BASE_URL"), ("openrouter:vendor/model", "OPENROUTER_API_KEY")],
)
def test_a_missing_provider_setting_is_one_startup_error_naming_it(
    no_provider_env: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    value: str,
    missing: str,
) -> None:
    with caplog.at_level(logging.ERROR, logger="shared.agents"):
        assert resolve_model("SYSTEM2_MODEL", value) is None
    assert "SYSTEM2_MODEL" in caplog.text and missing in caplog.text


def test_a_legacy_vllm_deploy_resolves_end_to_end(no_provider_env: pytest.MonkeyPatch) -> None:
    """Today's deploy .env: CLAUDE_MODEL=openai/<model> against OPENAI_COMPAT_HOST."""
    no_provider_env.setenv("CLAUDE_MODEL", "openai/gemma-x")
    no_provider_env.setenv("OPENAI_BASE_URL", "http://vllm.test:8000/v1")
    no_provider_env.setenv("OPENAI_COMPAT_HOST", "http://vllm.test:8000")
    config = AlfredConfig.from_env()
    apply_model_env(config)
    model = resolve_model("SYSTEM2_MODEL", config.system2_model)
    assert model is not None
    assert model_key(model) == "vllm:gemma-x"
    assert model.base_url == "http://vllm.test:8000/v1/"


def test_the_legacy_fields_are_gone() -> None:
    config = AlfredConfig.from_env()
    gone = ("claude_api_key", "claude_model", "claude_max_tokens")
    assert not [f for f in gone if hasattr(config, f)]


def test_another_provider_warns_once_that_it_keeps_the_sdks_retries(
    no_provider_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    no_provider_env.setenv("OLLAMA_BASE_URL", "http://ollama.test:11434/v1")
    with caplog.at_level(logging.WARNING, logger="shared.agents"):
        model = resolve_model("SYSTEM2_MODEL", "ollama:model-x")
    assert model is not None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "ollama" in warnings[0] and "SDK's own retries" in warnings[0]


def test_the_openai_compatible_providers_do_not_warn(
    no_provider_env: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    no_provider_env.setenv("OPENAI_API_KEY", "k")
    with caplog.at_level(logging.WARNING, logger="shared.agents"):
        assert resolve_model("SYSTEM2_MODEL", "openai:model-x") is not None
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []
