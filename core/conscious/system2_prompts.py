"""Which prose sections one System 2 turn includes, rendered from the prompt store.

Every input is known before the run starts (who is asking, the channel, the content
type, the satellite's area, whether integrations answered), so the engine renders the
sections here and the agent's instructions function stays sync and does no I/O.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from shared.prompts.names import (
    SYSTEM2_IDENTITY_GUEST,
    SYSTEM2_IDENTITY_SIR,
    SYSTEM2_INTEGRATIONS,
    SYSTEM2_LOCATION,
    SYSTEM2_PERSONALITY,
    SYSTEM2_PROACTIVITY,
    SYSTEM2_VOICE_DELIVERY,
)
from shared.prompts.template import repo_prompt

if TYPE_CHECKING:
    from shared.prompts.store import PromptStore
    from shared.prompts.template import PromptRef, RenderedPrompt

# Channels that speak the reply (TTS).
VOICE_CHANNELS: Final = frozenset({"web_pwa", "satellite"})
DEFAULT_PROACTIVITY: Final = "opinionated"


@dataclass(frozen=True)
class System2Prompts:
    """One turn's rendered sections; None is a section this turn does not include."""

    personality: RenderedPrompt
    identity: RenderedPrompt
    voice_delivery: RenderedPrompt | None = None
    location: RenderedPrompt | None = None
    integrations: RenderedPrompt | None = None
    proactivity: RenderedPrompt | None = None

    def refs(self) -> tuple[PromptRef, ...]:
        """Every section's version, personality first: the one a trace links to."""
        sections = (
            self.personality,
            self.voice_delivery,
            self.identity,
            self.location,
            self.integrations,
            self.proactivity,
        )
        return tuple(s.ref for s in sections if s is not None)


def _wanted(
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str,
) -> dict[str, tuple[str, dict[str, object]]]:
    """Section field → (prompt name, variables), for the sections this turn includes."""
    sir = who == "sir"
    wanted: dict[str, tuple[str, dict[str, object]]] = {
        "personality": (SYSTEM2_PERSONALITY, {}),
        "identity": (SYSTEM2_IDENTITY_SIR if sir else SYSTEM2_IDENTITY_GUEST, {}),
    }
    # Only when a spoken reply will actually be produced, not for typed web sessions.
    if content_type == "audio" and channel in VOICE_CHANNELS:
        wanted["voice_delivery"] = (SYSTEM2_VOICE_DELIVERY, {})
    if area:
        wanted["location"] = (SYSTEM2_LOCATION, {"area": area})
    if sir and integrations_available:
        wanted["integrations"] = (SYSTEM2_INTEGRATIONS, {})
    if sir:
        wanted["proactivity"] = (SYSTEM2_PROACTIVITY, {"level": proactivity_level})
    return wanted


async def fetch_system2_prompts(
    store: PromptStore,
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str = DEFAULT_PROACTIVITY,
) -> System2Prompts:
    """This turn's sections, from *store*."""
    wanted = _wanted(
        who=who,
        channel=channel,
        content_type=content_type,
        area=area,
        integrations_available=integrations_available,
        proactivity_level=proactivity_level,
    )
    rendered = await asyncio.gather(*(store.get(n, **v) for n, v in wanted.values()))
    return System2Prompts(**dict(zip(wanted, rendered, strict=True)))


def repo_system2_prompts(
    *,
    who: str,
    channel: str,
    content_type: str,
    area: str | None,
    integrations_available: bool,
    proactivity_level: str = DEFAULT_PROACTIVITY,
) -> System2Prompts:
    """This turn's sections from the repo defaults, without a store (tests, tools)."""
    wanted = _wanted(
        who=who,
        channel=channel,
        content_type=content_type,
        area=area,
        integrations_available=integrations_available,
        proactivity_level=proactivity_level,
    )
    return System2Prompts(**{f: repo_prompt(n, **v) for f, (n, v) in wanted.items()})
