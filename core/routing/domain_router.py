"""Generic domain routing — dispatches ActionRequests to the correct domain agent."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from loguru import logger

from bus.schemas.events import ActionRequest, ActionResult


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

    def __init__(self) -> None:
        self._agents: dict[str, DomainAgent] = {}

    def register(self, service_name: str, agent: DomainAgent) -> None:
        """Register a domain agent for a service name."""
        self._agents[service_name] = agent
        logger.info("Registered domain agent for '{}'", service_name)

    @property
    def registered_services(self) -> set[str]:
        """Return set of registered service names."""
        return set(self._agents)

    async def route(self, action: ActionRequest) -> ActionResult:
        """Route an ActionRequest to the appropriate domain agent."""
        agent = self._agents.get(action.target_service)
        if agent is None:
            logger.warning("No domain agent registered for service '%s'", action.target_service)
            return ActionResult(
                source="domain-router",
                request_id=action.request_id,
                tool_name=action.tool_name,
                status="error",
                error=f"No domain agent registered for service '{action.target_service}'",
            )
        return await agent.execute_action(action)

    async def execute_action(self, action: ActionRequest) -> ActionResult:
        """Alias for route() — satisfies DomainAgent protocol."""
        return await self.route(action)
