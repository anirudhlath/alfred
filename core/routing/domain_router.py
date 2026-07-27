"""Generic domain routing — dispatches ActionRequests to the correct domain agent.

Also the dispatch-layer enforcement point for tiered autonomy (defense in
depth — the Reflex prompt filter is the first layer):

1. ActionRequests from Reflex (``source`` starts with "reflex") targeting a
   tool with risk above "benign" are rejected and recorded as a
   ReflexObservation — a hallucinated tool name cannot actuate a lock.
2. Unconfirmed requests targeting a ``risk == "critical"`` tool are parked in
   ``alfred:pending_actions:{request_id}`` (TTL 5 min) and an URGENT
   notification asks the user to confirm. Confirmed requests
   (``confirmed=True``) pass through without re-interception.

Enforcement requires a Redis handle; ``DomainRouter()`` without one routes
unconditionally (offline/eval contexts and legacy tests).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from loguru import logger

from bus.schemas.events import ActionRequest, ActionResult
from core.notifications.schema import Urgency
from core.reflex.runner import publish_observation
from core.routing.pending import store_pending_action
from core.routing.risk import tool_risk
from shared.streams import REFLEX_OBSERVATIONS_STREAM

if TYPE_CHECKING:
    from core.notifications.publisher import NotificationPublisher
    from shared.types import AioRedis


@runtime_checkable
class DomainAgent(Protocol):
    """Protocol for domain agents that execute actions."""

    async def execute_action(self, action: ActionRequest) -> ActionResult: ...


class DomainRouter:
    """Routes ActionRequests to the appropriate domain agent by target_service.

    Agents register at startup. The router reads action.target_service
    and dispatches. Unknown services return an error result.
    Adding a new domain = adding a new agent + registering it.
    """

    def __init__(
        self,
        redis: AioRedis | None = None,
        notifier: NotificationPublisher | None = None,
    ) -> None:
        self._agents: dict[str, DomainAgent] = {}
        self._redis = redis
        self._notifier = notifier

    def register(self, service_name: str, agent: DomainAgent) -> None:
        """Register a domain agent for a service name."""
        self._agents[service_name] = agent
        logger.info("Registered domain agent for '{}'", service_name)

    @property
    def registered_services(self) -> set[str]:
        """Return set of registered service names."""
        return set(self._agents)

    def _error_result(self, action: ActionRequest, error: str) -> ActionResult:
        return ActionResult(
            source="domain-router",
            request_id=action.request_id,
            tool_name=action.tool_name,
            status="error",
            error=error,
        )

    async def _reject_reflex_action(
        self, redis: AioRedis, action: ActionRequest, risk: str
    ) -> ActionResult:
        """Reflex may only execute benign tools — reject, log, observe."""
        result = self._error_result(
            action,
            f"autonomy_violation: reflex may not execute '{action.tool_name}' (risk={risk})",
        )
        logger.warning(
            "Rejected Reflex ActionRequest '{}' targeting '{}' — risk '{}' exceeds benign",
            action.tool_name,
            action.target_service,
            risk,
        )
        # Record for System 2 awareness. publish_observation's trigger_event
        # slot carries the rejected request itself (the router has no
        # originating state event in scope).
        await publish_observation(
            redis, REFLEX_OBSERVATIONS_STREAM, "state_change", action, action, result
        )
        return result

    async def _intercept_critical(self, redis: AioRedis, action: ActionRequest) -> ActionResult:
        """Park an unconfirmed critical action and ask the user to confirm."""
        await store_pending_action(redis, action)
        if self._notifier is not None:
            await self._notifier.publish(
                title="Confirmation required",
                body=(
                    f"Alfred wants to run '{action.tool_name}' on "
                    f"{action.target_service} — confirm?"
                ),
                source="domain-router",
                urgency=Urgency.URGENT,
                metadata={
                    "pending_action_id": action.request_id,
                    "tool_name": action.tool_name,
                    "parameters": action.parameters,
                },
            )
        else:
            logger.warning(
                "No notifier configured — pending action {} stored silently",
                action.request_id,
            )
        logger.info(
            "Critical action '{}' intercepted — pending confirmation {}",
            action.tool_name,
            action.request_id,
        )
        return self._error_result(action, f"confirmation_required:{action.request_id}")

    async def route(self, action: ActionRequest) -> ActionResult:
        """Route an ActionRequest to the appropriate domain agent."""
        agent = self._agents.get(action.target_service)
        if agent is None:
            logger.warning("No domain agent registered for service '{}'", action.target_service)
            return self._error_result(
                action,
                f"No domain agent registered for service '{action.target_service}'",
            )

        if self._redis is not None:
            risk = await tool_risk(self._redis, action.target_service, action.tool_name)
            if action.source.startswith("reflex") and risk != "benign":
                return await self._reject_reflex_action(self._redis, action, risk)
            if risk == "critical" and not action.confirmed:
                return await self._intercept_critical(self._redis, action)

        return await agent.execute_action(action)

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        """Alias for route() — satisfies DomainAgent protocol."""
        return await self.route(action)
