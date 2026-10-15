"""What every Pydantic AI agent in Alfred shares: model resolution, the role header and
instrumentation. Agents are built once per process and run per request."""

from __future__ import annotations

import logging
from typing import Any, Final

import pydantic_ai
from opentelemetry import trace
from pydantic_ai.capabilities.instrumentation import Instrumentation
from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model, infer_model
from pydantic_ai.models.instrumented import InstrumentationSettings
from pydantic_ai.providers import Provider, infer_provider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.providers.vllm import VLLMProvider

from shared.llm_http import retrying_http_client
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
        return infer_model(text, provider_factory=_provider)
    except (UserError, ImportError, ValueError) as exc:
        logger.error("%s=%r cannot be used: %s", setting, text, exc)
        return None


# The `openai:` prefixes; every one is built on OpenAIProvider.
_OPENAI: Final = frozenset({"openai", "openai-chat", "openai-responses"})


def _provider(name: str) -> Provider[Any]:
    """The provider for *name*; OpenAI-compatible ones retry in the transport, never the SDK.

    Each provider builds its client first, so base URL, API key and headers resolve exactly
    as Pydantic AI resolves them; the copy only turns the SDK's own retries off. Any other
    provider keeps its SDK's retries, said once here, at resolution.
    """
    if name == "vllm":
        vllm = VLLMProvider(http_client=retrying_http_client())
        return VLLMProvider(openai_client=vllm.client.with_options(max_retries=0))
    if name == "openrouter":
        openrouter = OpenRouterProvider(http_client=retrying_http_client())
        return OpenRouterProvider(openai_client=openrouter.client.with_options(max_retries=0))
    if name in _OPENAI:
        openai = OpenAIProvider(http_client=retrying_http_client())
        return OpenAIProvider(openai_client=openai.client.with_options(max_retries=0))
    provider = infer_provider(name)
    logger.warning(
        "Provider %r keeps the SDK's own retries, not the bounded retrying transport "
        "(shared/llm_http.py), which only vllm, openrouter and openai use",
        name,
    )
    return provider


def model_key(model: Model) -> str:
    """``<provider>:<served name>``, the one string that names a resolved model."""
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
