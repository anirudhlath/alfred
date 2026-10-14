"""What every agent shares: model resolution, the role header, instrumentation."""

from __future__ import annotations

import importlib.util
import logging
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent

from shared.agents import instrumentation, model_key, resolve_model, role_headers
from shared.config import AlfredConfig, apply_model_env
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


@pytest.fixture
def no_provider_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A developer's .env reaches os.environ on import (``shared.config``); clear it."""
    for name in _PROVIDER_ENV:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


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
