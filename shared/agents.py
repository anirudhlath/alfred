"""What every Pydantic AI agent in Alfred shares: model resolution, the role header and
instrumentation. Agents are built once per process and run per request."""

from __future__ import annotations

import logging

import pydantic_ai
from opentelemetry import trace
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.instrumented import InstrumentationSettings

from shared.llm_roles import ROLE_HEADER, LlmRole

logger = logging.getLogger(__name__)

# Pydantic AI prints a banner on its first run; services log, they do not print.
pydantic_ai.BANNER_ENABLED = False


def resolve_model(setting: str, value: str) -> Model | None:
    """The model *value* names, or None, logged at ERROR, when it names none.

    *setting* is the variable the value came from, so the log line says what to fix.
    Called once at startup: a missing ``OPENROUTER_API_KEY`` or ``VLLM_BASE_URL`` fails
    here, when Pydantic AI builds the provider, rather than on the first request.
    """
    text = value.strip()
    if not text:
        logger.error("%s is not set — this role has no model", setting)
        return None
    try:
        return infer_model(text)
    except (UserError, ImportError, ValueError) as exc:
        logger.error("%s=%r cannot be used: %s", setting, text, exc)
        return None


def model_key(model: Model) -> str:
    """``<provider>:<served name>``, the key ``CostTracker`` prices a model by."""
    return f"{model.system}:{model.model_name}"


def role_headers(role: LlmRole) -> dict[str, str]:
    """The header the eval proxy attributes a request by."""
    return {ROLE_HEADER: role}


def instrumentation() -> Instrumentation:
    """GenAI spans, content included, on the tracer provider ``init_tracing`` installed."""
    return Instrumentation(
        settings=InstrumentationSettings(
            tracer_provider=trace.get_tracer_provider(), include_content=True
        )
    )
