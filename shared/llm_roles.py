"""The LLM roles, and the header each request names its role in.

The eval proxy attributes a recorded call to a role by this header
(``evals/harness/proxy.py``), so a prompt edit can never break attribution.
Dependency-free on purpose: Reflex's raw HTTP client and the eval harness import it.
"""

from __future__ import annotations

from typing import Final, Literal

ROLE_HEADER: Final = "X-Alfred-Role"

LlmRole = Literal["system1", "system2", "librarian"]

SYSTEM1: Final[LlmRole] = "system1"
SYSTEM2: Final[LlmRole] = "system2"
LIBRARIAN: Final[LlmRole] = "librarian"

LLM_ROLES: Final[frozenset[str]] = frozenset({SYSTEM1, SYSTEM2, LIBRARIAN})
