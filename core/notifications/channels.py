"""Channel adapter base class and auto-discovery registry."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from core.notifications.schema import Notification, Urgency

logger = logging.getLogger(__name__)


class ChannelAdapter(ABC):
    """Base class for notification delivery channels."""

    name: ClassVar[str]
    supported_urgencies: ClassVar[set[Urgency]]

    def supports_urgency(self, urgency: Urgency) -> bool:
        """Check if this adapter handles the given urgency level."""
        return urgency in self.supported_urgencies

    @abstractmethod
    async def deliver(self, notification: Notification) -> None:
        """Deliver a notification through this channel."""
        ...


class ChannelRegistry:
    """Auto-discovery registry for channel adapters.

    Uses decorator-based registration (same pattern as IntegrationRegistry).
    """

    _registry: ClassVar[dict[str, type[ChannelAdapter]]] = {}
    _instances: ClassVar[dict[str, ChannelAdapter]] = {}

    @classmethod
    def register(cls, **kwargs: Any) -> Any:
        """Class decorator. Registers adapter at import time."""

        def decorator(adapter_cls: type[ChannelAdapter]) -> type[ChannelAdapter]:
            name = adapter_cls.name
            cls._registry[name] = adapter_cls
            logger.info("Registered channel adapter: %s", name)
            return adapter_cls

        return decorator

    @classmethod
    def get_adapters_for_urgency(cls, urgency: Urgency) -> list[ChannelAdapter]:
        """Return cached instances of all adapters supporting the given urgency.

        Only returns adapters that have been explicitly initialized via
        set_instance(). Adapters registered via @register but not yet
        initialized are skipped — this prevents silent failures from
        adapters constructed with no args before startup wiring completes.
        """
        result: list[ChannelAdapter] = []
        for name in cls._registry:
            if name not in cls._instances:
                logger.debug("Adapter '%s' registered but not initialized, skipping", name)
                continue
            instance = cls._instances[name]
            if instance.supports_urgency(urgency):
                result.append(instance)
        return result

    @classmethod
    def available(cls) -> list[str]:
        """Return all registered adapter names."""
        return list(cls._registry.keys())

    @classmethod
    def set_instance(cls, name: str, instance: ChannelAdapter) -> None:
        """Inject a pre-built adapter instance (for adapters needing constructor args)."""
        cls._instances[name] = instance
