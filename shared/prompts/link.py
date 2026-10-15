"""Which prompt versions a model call used, on its span, so Langfuse links the two.

The caller wraps ``agent.run`` in ``linked_prompts``. Pydantic AI sets
``gen_ai.operation.name`` when it starts a span, so the processor can tell a model call
(``chat``) from the agent run around it. A ContextVar rather than baggage: baggage
is propagated in outgoing HTTP headers, and prompt names have no business there.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from opentelemetry.sdk.trace import SpanProcessor

if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry import context
    from opentelemetry.sdk.trace import Span

    from shared.prompts.template import PromptRef

PROMPT_NAME_ATTR: Final = "langfuse.observation.prompt.name"
PROMPT_VERSION_ATTR: Final = "langfuse.observation.prompt.version"
PROMPTS_ATTR: Final = "alfred.prompts"
_OPERATION: Final = "gen_ai.operation.name"
_CHAT: Final = "chat"


@dataclass(frozen=True)
class _Link:
    primary: PromptRef
    others: tuple[PromptRef, ...]


_LINK: ContextVar[_Link | None] = ContextVar("alfred_prompt_link", default=None)


@contextmanager
def linked_prompts(primary: PromptRef, *others: PromptRef) -> Iterator[None]:
    """Model calls inside link to *primary* and list every prompt they used."""
    token = _LINK.set(_Link(primary, others))
    try:
        yield
    finally:
        _LINK.reset(token)


class PromptLinkProcessor(SpanProcessor):
    """Copies the current prompt link onto each model-call span as it starts."""

    def on_start(self, span: Span, parent_context: context.Context | None = None) -> None:
        link = _LINK.get()
        if link is None or (span.attributes or {}).get(_OPERATION) != _CHAT:
            return
        primary = link.primary
        # A repo default has no Langfuse version to link to; a Redis copy has one.
        if primary.source != "repo" and primary.version is not None:
            span.set_attribute(PROMPT_NAME_ATTR, primary.name)
            span.set_attribute(PROMPT_VERSION_ATTR, primary.version)
        span.set_attribute(PROMPTS_ATTR, [str(ref) for ref in (primary, *link.others)])
