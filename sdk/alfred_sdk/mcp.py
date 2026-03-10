"""MCP tool decorator for declaring microservice capabilities."""

from __future__ import annotations

import functools
import inspect
from typing import TYPE_CHECKING, Any, get_type_hints

if TYPE_CHECKING:
    from collections.abc import Callable


def mcp_tool(name: str, description: str) -> Callable[..., Any]:
    """Decorator to declare an MCP tool capability.

    Extracts parameter info from type hints to build a tool manifest.
    The decorated function still works normally when called directly.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)

        parameters: dict[str, Any] = {}
        for param_name, param in sig.parameters.items():
            if param_name in ("self", "cls"):
                continue
            param_info: dict[str, Any] = {}
            if param_name in hints:
                hint = hints[param_name]
                param_info["type"] = getattr(hint, "__name__", str(hint))
            if param.default is not inspect.Parameter.empty:
                param_info["default"] = param.default
            parameters[param_name] = param_info

        meta: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": parameters,
        }

        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return fn(*args, **kwargs)

        wrapper._mcp_tool_meta = meta  # type: ignore[attr-defined]
        return wrapper

    return decorator
