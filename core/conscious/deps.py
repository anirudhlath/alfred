"""What one System 2 turn knows: who is asking, when and where, and the service handles.

Built by ``ConsciousEngine.process_request`` once per request; the agent's instructions
and every toolset read it from ``RunContext.deps``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from core.conscious.system2_prompts import System2Prompts
    from core.conscious.tools.integrations import IntegrationTool
    from core.identity.schemas import IdentityResult
    from core.memory.context_index import ContextIndexManager
    from core.memory.vector_store import SearchResult
    from core.reflex.context_reader import ContextReader
    from core.reflex.tool_registry import ToolInfo
    from core.routing.domain_router import DomainRouter
    from shared.types import AioRedis


@dataclass(frozen=True)
class TurnServices:
    """The process's service handles, shared by every turn."""

    redis: AioRedis
    router: DomainRouter
    context_reader: ContextReader
    context_index: ContextIndexManager | None = None


@dataclass
class TurnDeps:
    """One turn's dependencies.

    ``registry_tools`` and ``integration_tools`` are read once per turn, before the run,
    so listing tools on each model request costs no I/O. ``actions_taken`` is filled by
    the catalog as it dispatches calls.

    ``received_ms`` is when the request entered the requests stream: its entry id's
    milliseconds, which Redis assigns on ``XADD``. It is authoritative over
    ``UserRequest.timestamp``, which the producing channel stamps on its own clock, because
    the runner's replay window reads the entry id too, so both checks agree.
    ``RecordEffects`` refuses a side-effecting call once it is past the window.

    ``prompts`` is the turn's prose sections, rendered from the prompt store before the run.

    ``approval_id`` is set only on a run resumed after the owner approved or declined its
    parked calls (``core.conscious.approvals``): an approved call's outcome is published
    under it, so the client that showed the approval can tie the outcome to it.
    """

    identity: IdentityResult
    session_id: str
    channel: str
    request_id: str
    tz_name: str
    now: datetime
    services: TurnServices
    received_ms: int
    prompts: System2Prompts
    content_type: str = "text"
    area: str | None = None
    recall: list[SearchResult] = field(default_factory=list)
    routine_hint: str = ""
    registry_tools: list[ToolInfo] = field(default_factory=list)
    integration_tools: list[IntegrationTool] = field(default_factory=list)
    actions_taken: list[str] = field(default_factory=list)
    approval_id: str | None = None

    @property
    def who(self) -> str:
        """The resolved identity: ``sir`` or ``guest``."""
        return self.identity.identity
