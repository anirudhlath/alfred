"""AlfredClient — the entry point for microservices to integrate with Alfred."""

from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any

from .mcp import mcp_tool

if TYPE_CHECKING:
    from collections.abc import Callable


class AlfredClient:
    """Client that microservices use to register with Alfred."""

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

        self.tools: list[dict[str, Any]] = []
        self.publishers: list[dict[str, Any]] = []
        self.subscribers: list[dict[str, Any]] = []
        self._tool_fns: dict[str, Callable[..., Any]] = {}

    def tool(self, name: str, description: str) -> Callable[..., Any]:
        """Register an MCP tool capability."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            wrapped: Callable[..., Any] = mcp_tool(name=name, description=description)(fn)
            meta: dict[str, Any] = wrapped._mcp_tool_meta  # type: ignore[attr-defined]
            self.tools.append(meta)
            self._tool_fns[name] = wrapped
            return wrapped

        return decorator

    def publisher(self, topic: str) -> Callable[..., Any]:
        """Register an event publisher."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.publishers.append({"topic": topic, "function": fn.__name__})

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            wrapper._publisher_meta = {"topic": topic}  # type: ignore[attr-defined]
            return wrapper

        return decorator

    def subscriber(self, topic: str) -> Callable[..., Any]:
        """Register an event subscriber."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            self.subscribers.append({"topic": topic, "function": fn.__name__})

            @functools.wraps(fn)
            def wrapper(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            wrapper._subscriber_meta = {"topic": topic}  # type: ignore[attr-defined]
            return wrapper

        return decorator

    def get_registration_manifest(self) -> dict[str, Any]:
        """Build the tool registration manifest for Alfred's registry."""
        return {
            "service_name": self.service_name,
            "service_endpoint": self.service_endpoint,
            "tools": self.tools,
            "publishers": [p["topic"] for p in self.publishers],
            "subscribers": [s["topic"] for s in self.subscribers],
        }

    async def register(self) -> None:
        """Register this service's capabilities with Alfred's tool registry on Redis."""
        import json

        import redis.asyncio as aioredis

        r: aioredis.Redis[Any] = aioredis.from_url(self.redis_url)  # type: ignore[type-arg]
        manifest = self.get_registration_manifest()
        await r.hset("alfred:tool_registry", self.service_name, json.dumps(manifest))  # type: ignore[misc]
        await r.aclose()
