"""What every agent shares: model resolution, the role header, instrumentation."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic_ai import Agent

from shared.agents import instrumentation, model_key, resolve_model, role_headers
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
