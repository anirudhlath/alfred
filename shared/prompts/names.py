"""Every prompt the store serves, with the variables its caller passes.

A name is also its repo default's file name (``defaults/<name>.md``) and its name in
Langfuse. A Langfuse version that uses a variable not declared here is refused.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping

SYSTEM2_PERSONALITY: Final = "system2-personality"
SYSTEM2_VOICE_DELIVERY: Final = "system2-voice-delivery"
SYSTEM2_IDENTITY_SIR: Final = "system2-identity-sir"
SYSTEM2_IDENTITY_GUEST: Final = "system2-identity-guest"
SYSTEM2_LOCATION: Final = "system2-location"
SYSTEM2_INTEGRATIONS: Final = "system2-integrations"
SYSTEM2_PROACTIVITY: Final = "system2-proactivity"
LIBRARIAN_ANALYSE: Final = "librarian-analyse"
LIBRARIAN_EXTRACT: Final = "librarian-extract"
LIBRARIAN_COMPRESS: Final = "librarian-compress"
LIBRARIAN_RESOLVE: Final = "librarian-resolve"
LIBRARIAN_PATTERNS: Final = "librarian-patterns"

PROMPTS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        SYSTEM2_PERSONALITY: frozenset(),
        SYSTEM2_VOICE_DELIVERY: frozenset(),
        SYSTEM2_IDENTITY_SIR: frozenset(),
        SYSTEM2_IDENTITY_GUEST: frozenset(),
        SYSTEM2_LOCATION: frozenset({"area"}),
        SYSTEM2_INTEGRATIONS: frozenset(),
        SYSTEM2_PROACTIVITY: frozenset({"level"}),
        LIBRARIAN_ANALYSE: frozenset(),
        LIBRARIAN_EXTRACT: frozenset(),
        LIBRARIAN_COMPRESS: frozenset(),
        LIBRARIAN_RESOLVE: frozenset({"min_observations", "min_days"}),
        LIBRARIAN_PATTERNS: frozenset({"min_occurrences", "min_days", "tz_name"}),
    }
)
