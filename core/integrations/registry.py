"""IntegrationRegistry — decorator-based registration for data-fetching adapters.

Mirrors the TriggerRegistry pattern. Adapters register via
@IntegrationRegistry.register() class decorator.
"""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from core.integrations.base import Integration, IntegrationCapability  # noqa: TC001

logger = logging.getLogger(__name__)


class IntegrationRegistry:
    """Discovers and manages integration adapters.

    Stores classes (not instances), consistent with TriggerRegistry pattern.
    Instances are created lazily on first `.get()` call and cached.
    """

    _registry: ClassVar[dict[str, type[Integration]]] = {}
    _instances: ClassVar[dict[str, Integration]] = {}

    @classmethod
    def register(cls) -> type:
        """Class decorator to register an integration adapter class.

        Usage:
            @IntegrationRegistry.register()
            class WeatherIntegration(Integration):
                name = "weather"
                ...
        """

        def decorator(integration_cls: type[Integration]) -> type[Integration]:
            cls._registry[integration_cls.name] = integration_cls
            logger.info("Registered integration class: %s", integration_cls.name)
            return integration_cls

        return decorator  # type: ignore[return-value]

    @classmethod
    def get(cls, name: str, **kwargs: Any) -> Integration:
        """Look up an integration by name. Creates instance on first access.

        Pass kwargs to configure the adapter on first instantiation (e.g.,
        latitude=40.7 for weather). Subsequent calls return the cached instance.
        Raises KeyError if unknown.
        """
        if name in cls._instances:
            return cls._instances[name]
        try:
            integration_cls = cls._registry[name]
        except KeyError:
            raise KeyError(
                f"Unknown integration: {name!r}. Available: {list(cls._registry.keys())}"
            ) from None
        instance = integration_cls(**kwargs)
        cls._instances[name] = instance
        return instance

    @classmethod
    def available(cls) -> list[str]:
        """Return all registered integration names."""
        return list(cls._registry.keys())

    @classmethod
    async def get_all_capabilities(cls) -> list[IntegrationCapability]:
        """Aggregate capabilities from all registered integrations."""
        caps: list[IntegrationCapability] = []
        for name in cls._registry:
            instance = cls.get(name)
            caps.extend(await instance.get_capabilities())
        return caps

    @classmethod
    async def health_check_all(cls) -> dict[str, bool]:
        """Run health checks on all integrations."""
        results: dict[str, bool] = {}
        for name in cls._registry:
            try:
                instance = cls.get(name)
                results[name] = await instance.health_check()
            except Exception:
                results[name] = False
        return results

    @classmethod
    def build_capabilities_docs(cls) -> str:
        """Build a text description of all available integrations for the system prompt."""
        lines: list[str] = ["Available integrations:"]
        for name, integration_cls in sorted(cls._registry.items()):
            lines.append(f"  - {name} ({integration_cls.category})")
        return "\n".join(lines)
