"""BaseFeature abstraction and @tool decorator for microservice tool registration."""

from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass
from typing import Any, overload

from pydantic import BaseModel


# ── Pydantic Manifest Models (write-side, for Redis registration) ──


class ToolParameter(BaseModel):
    """Schema for a single tool parameter in the manifest."""

    type: str
    description: str = ""
    default: Any = None


class ToolManifest(BaseModel):
    """Schema for a single tool in the manifest."""

    name: str
    description: str = ""
    parameters: dict[str, ToolParameter] = {}


class FeatureManifest(BaseModel):
    """Schema for a feature (group of tools) in the manifest."""

    name: str
    description: str = ""
    tools: list[ToolManifest] = []


class ServiceManifest(BaseModel):
    """Schema for a service's full registration manifest."""

    service_name: str
    service_endpoint: str
    features: list[FeatureManifest] = []


# ── ToolMeta dataclass ──


@dataclass(frozen=True)
class ToolMeta:
    """Extracted metadata for a single tool method."""

    name: str
    description: str
    parameters: dict[str, ToolParameter]


# ── Docstring parser ──


def _parse_google_docstring_args(docstring: str) -> dict[str, str]:
    """Extract parameter descriptions from a Google-style Args section.

    Args:
        docstring: The full docstring to parse.

    Returns:
        Mapping of parameter name to description string.
    """
    args: dict[str, str] = {}
    in_args = False
    current_param: str | None = None
    current_desc_lines: list[str] = []

    for line in docstring.split("\n"):
        stripped = line.strip()

        if stripped == "Args:":
            in_args = True
            continue

        if in_args:
            # End of Args section: blank line or new section header
            if stripped == "" or (stripped.endswith(":") and not stripped.startswith(" ")):
                if current_param is not None:
                    args[current_param] = " ".join(current_desc_lines).strip()
                break

            # New parameter line: "name: description"
            param_match = re.match(r"^(\w+)\s*(?:\(.*?\))?\s*:\s*(.*)$", stripped)
            if param_match:
                if current_param is not None:
                    args[current_param] = " ".join(current_desc_lines).strip()
                current_param = param_match.group(1)
                current_desc_lines = [param_match.group(2)]
            elif current_param is not None:
                # Continuation line for current parameter
                current_desc_lines.append(stripped)

    # Flush last parameter if docstring ends without blank line
    if current_param is not None and current_param not in args:
        args[current_param] = " ".join(current_desc_lines).strip()

    return args


def _extract_tool_meta(
    fn: Any,
    feature_name: str,
    name_override: str | None = None,
    description_override: str | None = None,
) -> ToolMeta:
    """Extract ToolMeta from a @tool-decorated method.

    Args:
        fn: The decorated function/method.
        feature_name: The owning feature's name (used for qualified tool name).
        name_override: Optional explicit tool name.
        description_override: Optional explicit description.
    """
    from typing import get_type_hints

    qualified_name = name_override or f"{feature_name}.{fn.__name__}"
    docstring = inspect.getdoc(fn) or ""
    description = description_override or (docstring.split("\n")[0] if docstring else "")

    # Parse parameter descriptions from Google-style docstring
    doc_args = _parse_google_docstring_args(docstring) if docstring else {}

    # Extract type hints (skip self, cls, return)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    sig = inspect.signature(fn)
    parameters: dict[str, ToolParameter] = {}
    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        type_str = getattr(hints.get(param_name), "__name__", str(hints.get(param_name, "Any")))
        param_desc = doc_args.get(param_name, "")
        default = param.default if param.default is not inspect.Parameter.empty else None
        parameters[param_name] = ToolParameter(
            type=type_str,
            description=param_desc,
            default=default,
        )

    return ToolMeta(name=qualified_name, description=description, parameters=parameters)


# ── BaseFeature Base Class ──


class BaseFeature:
    """Base class for grouping related tools in a microservice.

    Subclass this and decorate methods with @tool. Tool metadata is
    auto-extracted from docstrings and type hints.
    """

    feature_name: str  # Must be set by subclass

    def get_tools(self) -> list[ToolMeta]:
        """Auto-discover @tool methods and extract their metadata."""
        tools: list[ToolMeta] = []
        for attr_name in dir(self):
            attr = getattr(self, attr_name, None)
            if attr is None or not getattr(attr, "_tool_marker", False):
                continue
            overrides = getattr(attr, "_tool_overrides", {})
            meta = _extract_tool_meta(
                attr,
                feature_name=self.feature_name,
                name_override=overrides.get("name"),
                description_override=overrides.get("description"),
            )
            tools.append(meta)
        return tools

    def get_description(self) -> str:
        """Return the feature description from the class docstring."""
        doc = inspect.getdoc(type(self)) or ""
        return doc.split("\n")[0] if doc else ""

    def to_manifest(self) -> FeatureManifest:
        """Build a FeatureManifest from this feature's tools."""
        tool_manifests = [
            ToolManifest(
                name=t.name,
                description=t.description,
                parameters=dict(t.parameters),
            )
            for t in self.get_tools()
        ]
        return FeatureManifest(
            name=self.feature_name,
            description=self.get_description(),
            tools=tool_manifests,
        )


# ── @tool Decorator ──

# @tool supports both @tool and @tool(description=..., name=...)
# Using @overload for type safety.

_F = Any  # Callable type alias for decorated functions


@overload
def tool(fn: _F) -> _F: ...


@overload
def tool(
    *,
    description: str | None = None,
    name: str | None = None,
) -> Any: ...


def tool(
    fn: _F | None = None,
    *,
    description: str | None = None,
    name: str | None = None,
) -> Any:
    """Mark a BaseFeature method as a tool.

    Supports both bare ``@tool`` and ``@tool(description=..., name=...)``.
    Metadata is auto-extracted from docstring + type hints at discovery time.
    """

    def decorator(f: _F) -> _F:
        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return f(*args, **kwargs)

        wrapper._tool_marker = True  # type: ignore[attr-defined]
        wrapper._tool_overrides = {  # type: ignore[attr-defined]
            "description": description,
            "name": name,
        }
        return wrapper  # type: ignore[return-value]

    if fn is not None:
        return decorator(fn)
    return decorator
