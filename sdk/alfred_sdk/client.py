"""AlfredClient — the entry point for microservices to integrate with Alfred."""

from __future__ import annotations

import importlib
import logging
import os
import pkgutil
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from .feature import BaseFeature

logger = logging.getLogger(__name__)


class AlfredClient:
    """Client that microservices use to register with Alfred."""

    REGISTRY_KEY = "alfred:tool_registry"  # must match ToolRegistry.REGISTRY_KEY in core/

    def __init__(
        self,
        service_name: str = "",
        service_endpoint: str = "",
        redis_url: str = "",
        mqtt_host: str = "",
        mqtt_port: int = 1883,
    ) -> None:
        self.service_name = service_name or os.getenv("ALFRED_SERVICE_NAME", "unknown")
        self.service_endpoint = service_endpoint or os.getenv("ALFRED_SERVICE_ENDPOINT", "")
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.mqtt_host = mqtt_host or os.getenv("MQTT_HOST", "localhost")
        self.mqtt_port = mqtt_port

        self._tool_fns: dict[str, Callable[..., Any]] = {}
        self._features: list[BaseFeature] = []

    # ── Feature Discovery ──

    def discover_features_from_classes(
        self,
        feature_classes: list[type[BaseFeature]],
        ctx: Any = None,
    ) -> list[BaseFeature]:
        """Instantiate feature classes and register their tools.

        Args:
            feature_classes: List of BaseFeature subclasses to instantiate.
            ctx: Shared context object passed to each feature's __init__.
        """
        from .feature import BaseFeature

        instances: list[BaseFeature] = []
        for cls in feature_classes:
            if not (isinstance(cls, type) and issubclass(cls, BaseFeature)):
                continue
            instance = cls(ctx) if ctx is not None else cls()  # type: ignore[call-arg]
            instances.append(instance)
            # Register tool methods in dispatch table
            for tool_meta in instance.get_tools():
                # Find the bound method matching the tool's unqualified name
                method_name = tool_meta.name.split(".")[-1]
                bound_method = getattr(instance, method_name)
                if tool_meta.name in self._tool_fns:
                    logger.warning(
                        "Tool name collision: '%s' — later registration wins",
                        tool_meta.name,
                    )
                self._tool_fns[tool_meta.name] = bound_method

        self._features.extend(instances)
        return instances

    def discover_features(
        self,
        package: str | ModuleType,
        ctx: Any = None,
    ) -> list[BaseFeature]:
        """Scan a package for BaseFeature subclasses and register their tools.

        Args:
            package: Package path string or module to scan.
            ctx: Shared context object passed to each feature's __init__.
        """
        from .feature import BaseFeature

        pkg_module = importlib.import_module(package) if isinstance(package, str) else package

        # Single pass: import submodules and collect BaseFeature subclasses
        feature_classes: list[type[BaseFeature]] = []
        if hasattr(pkg_module, "__path__"):
            for _importer, modname, _ispkg in pkgutil.walk_packages(
                pkg_module.__path__, prefix=pkg_module.__name__ + "."
            ):
                mod = importlib.import_module(modname)
                for attr_name in dir(mod):
                    attr = getattr(mod, attr_name)
                    if (
                        isinstance(attr, type)
                        and issubclass(attr, BaseFeature)
                        and attr is not BaseFeature
                        and attr.__module__ == mod.__name__
                    ):
                        feature_classes.append(attr)

        return self.discover_features_from_classes(feature_classes, ctx=ctx)

    # ── Dispatch ──

    def dispatch_sync(self, method: str, params: dict[str, Any]) -> Any:
        """Synchronous dispatch — for testing. Raises KeyError if not found."""
        fn = self._tool_fns.get(method)
        if fn is None:
            raise KeyError(f"Unknown tool: {method}")
        return fn(**params)

    async def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        """Async dispatch — calls the bound tool method."""
        import asyncio

        fn = self._tool_fns.get(method)
        if fn is None:
            raise KeyError(f"Unknown tool: {method}")
        result: Any = fn(**params)
        if asyncio.iscoroutine(result) or asyncio.isfuture(result):
            result = await result
        return result

    # ── Manifest and Registration ──

    def get_registration_manifest(self) -> dict[str, Any]:
        """Build the tool registration manifest for Alfred's registry."""
        feature_manifests: list[dict[str, Any]] = []
        for f in self._features:
            feature_manifests.append(f.to_manifest().model_dump())

        return {
            "service_name": self.service_name,
            "service_endpoint": self.service_endpoint,
            "features": feature_manifests,
        }

    async def register(self) -> None:
        """Register this service's capabilities with Alfred's tool registry on Redis."""
        import json

        import redis.asyncio as aioredis

        r: aioredis.Redis[Any] = aioredis.from_url(self.redis_url)  # type: ignore[type-arg]
        manifest = self.get_registration_manifest()
        await r.hset(self.REGISTRY_KEY, self.service_name, json.dumps(manifest))  # type: ignore[misc]
        await r.aclose()

    async def unregister(self) -> None:
        """Remove this service from Alfred's tool registry on Redis.

        Idempotent — safe to call if already unregistered.
        """
        import redis.asyncio as aioredis

        r: aioredis.Redis[Any] = aioredis.from_url(self.redis_url)  # type: ignore[type-arg]
        await r.hdel(self.REGISTRY_KEY, self.service_name)  # type: ignore[misc]
        await r.aclose()
