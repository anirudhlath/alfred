# BaseFeature & Dynamic Tool Registry Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace hardcoded tool definitions with auto-discovered BaseFeature classes and a dynamic ToolRegistry, so adding a tool is just adding a method.

**Architecture:** SDK defines `BaseFeature` + `@tool` for microservice authors. `AlfredClient.discover_features()` scans a package and auto-registers tools. Core's `ToolRegistry` reads from Redis at runtime. Reflex Engine builds its prompt dynamically from the registry.

**Tech Stack:** Python 3.13, Pydantic v2, redis-py async, pytest + pytest-asyncio, Google-style docstrings

**Spec:** `docs/superpowers/specs/2026-03-10-basefeature-dynamic-tools-design.md`

---

## File Structure

### New Files
| File | Responsibility |
|------|---------------|
| `sdk/alfred_sdk/feature.py` | `BaseFeature`, `@tool`, `ToolMeta`, docstring parser, Pydantic manifest models |
| `sdk/alfred_sdk/tests/__init__.py` | Test package marker |
| `sdk/alfred_sdk/tests/test_feature.py` | Tests for feature.py |
| `core/reflex/tool_registry.py` | `ToolRegistry`, `ToolInfo` |
| `core/reflex/tests/test_tool_registry.py` | Tests for tool_registry.py |
| `home-service/alfred_ext/features/__init__.py` | Package marker |
| `home-service/alfred_ext/features/lighting.py` | `LightingFeature` |
| `home-service/alfred_ext/features/scenes.py` | `SceneFeature` |

### Modified Files
| File | Changes |
|------|---------|
| `sdk/alfred_sdk/client.py` | Add `discover_features()`, `unregister()`, feature-aware `register()` and `dispatch()` |
| `sdk/alfred_sdk/__init__.py` | Export `BaseFeature`, `tool` |
| `core/reflex/engine.py` | Dynamic prompt from `ToolRegistry`, remove hardcoded `SYSTEM_PROMPT` and `_TARGET_SERVICE` |
| `core/reflex/__main__.py` | Wire `ToolRegistry`, fail-fast startup check, graceful `unregister()` |
| `core/reflex/tests/test_engine.py` | Update tests for new engine API |
| `home-service/alfred_ext/register.py` | Migrate from `@client.tool()` to `discover_features()` |
| `home-service/tests/test_server.py` | Update tool names (`smart_home.*` → `lighting.*` / `scenes.*`) |

### Documentation Updates
| File | Changes |
|------|---------|
| `sdk/CLAUDE.md` | Add BaseFeature, @tool, discover_features |
| `core/CLAUDE.md` | Add ToolRegistry |
| `.claude/rules/sdk/sdk-design.md` | Add BaseFeature pattern |
| `.claude/rules/core/reflex-engine.md` | Add "reads tools from ToolRegistry" |

---

## Chunk 1: SDK — `@tool` Decorator, `BaseFeature`, and Manifest Models

### Task 1: `@tool` Decorator and `ToolMeta`

**Files:**
- Create: `sdk/alfred_sdk/feature.py`
- Create: `sdk/alfred_sdk/tests/__init__.py`
- Create: `sdk/alfred_sdk/tests/test_feature.py`

- [ ] **Step 1: Write failing tests for `@tool` decorator**

In `sdk/alfred_sdk/tests/test_feature.py`:

```python
"""Tests for BaseFeature and @tool decorator."""

from __future__ import annotations

from alfred_sdk.feature import ToolMeta, tool


def test_tool_decorator_marks_method() -> None:
    """@tool sets _tool_marker on the function."""

    @tool
    def my_func(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_func._tool_marker is True  # type: ignore[attr-defined]


def test_tool_decorator_preserves_function() -> None:
    """@tool doesn't change function behavior."""

    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    assert add(1, 2) == 3


def test_tool_decorator_with_overrides() -> None:
    """@tool(description=...) overrides docstring extraction."""

    @tool(description="Custom description", name="custom.name")
    def my_func(x: int) -> str:
        """Original description."""
        return str(x)

    assert my_func._tool_marker is True  # type: ignore[attr-defined]
    assert my_func._tool_overrides["description"] == "Custom description"  # type: ignore[attr-defined]
    assert my_func._tool_overrides["name"] == "custom.name"  # type: ignore[attr-defined]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_feature.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'alfred_sdk.feature'`

- [ ] **Step 3: Implement `@tool` decorator and `ToolMeta`**

Create `sdk/alfred_sdk/tests/__init__.py` (empty).

In `sdk/alfred_sdk/feature.py`:

```python
"""BaseFeature abstraction and @tool decorator for microservice tool registration."""

from __future__ import annotations

import functools
import inspect
import re
from dataclasses import dataclass, field
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
    tools: list[ToolManifest] = []  # Legacy @client.tool() entries


# ── @tool Decorator ──


@dataclass(frozen=True)
class ToolMeta:
    """Extracted metadata for a single tool method."""

    name: str
    description: str
    parameters: dict[str, ToolParameter]


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_feature.py -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/tests/__init__.py sdk/alfred_sdk/tests/test_feature.py
git commit -m "feat(sdk): add @tool decorator, ToolMeta, and Pydantic manifest models"
```

### Task 2: `BaseFeature` Base Class

**Files:**
- Modify: `sdk/alfred_sdk/feature.py`
- Modify: `sdk/alfred_sdk/tests/test_feature.py`

- [ ] **Step 1: Write failing tests for `BaseFeature`**

Append to `sdk/alfred_sdk/tests/test_feature.py`:

```python
from alfred_sdk.feature import BaseFeature, ToolMeta, tool


class _StubFeature(BaseFeature):
    """A test feature for lighting."""

    feature_name = "test_lighting"

    def __init__(self) -> None:
        super().__init__()
        self.ha_called = False

    @tool
    def dim_lights(self, room: str, level: int) -> dict:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        self.ha_called = True
        return {"room": room, "level": level}

    @tool(description="Custom turn off description")
    def turn_off(self, room: str) -> dict:
        """Original description."""
        return {"room": room}

    def helper_method(self) -> None:
        """Not a tool — no @tool decorator."""


def test_base_feature_get_tools_returns_tool_meta() -> None:
    feature = _StubFeature()
    tools = feature.get_tools()
    assert len(tools) == 2

    names = {t.name for t in tools}
    assert "test_lighting.dim_lights" in names
    assert "test_lighting.turn_off" in names


def test_base_feature_get_tools_extracts_params() -> None:
    feature = _StubFeature()
    tools = {t.name: t for t in feature.get_tools()}

    dim = tools["test_lighting.dim_lights"]
    assert "room" in dim.parameters
    assert dim.parameters["room"].type == "str"
    assert dim.parameters["room"].description == "The room to dim."
    assert "level" in dim.parameters
    assert dim.parameters["level"].type == "int"


def test_base_feature_get_tools_uses_overrides() -> None:
    feature = _StubFeature()
    tools = {t.name: t for t in feature.get_tools()}

    turn_off = tools["test_lighting.turn_off"]
    assert turn_off.description == "Custom turn off description"


def test_base_feature_get_tools_skips_non_tool_methods() -> None:
    feature = _StubFeature()
    tools = feature.get_tools()
    names = {t.name for t in tools}
    assert "test_lighting.helper_method" not in names


def test_base_feature_get_tools_no_docstring() -> None:
    class _NoDocFeature(BaseFeature):
        feature_name = "nodoc"

        @tool
        def do_thing(self, x: int) -> int:
            return x

    feature = _NoDocFeature()
    tools = feature.get_tools()
    assert len(tools) == 1
    assert tools[0].description == ""


def test_base_feature_description_from_class_docstring() -> None:
    feature = _StubFeature()
    assert feature.get_description() == "A test feature for lighting."


def test_base_feature_to_manifest() -> None:
    feature = _StubFeature()
    manifest = feature.to_manifest()
    assert manifest.name == "test_lighting"
    assert manifest.description == "A test feature for lighting."
    assert len(manifest.tools) == 2
```

- [ ] **Step 2: Run tests to verify new tests fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_feature.py -v`
Expected: New tests FAIL — `BaseFeature` has no `get_tools` method yet

- [ ] **Step 3: Implement `BaseFeature`**

Add to `sdk/alfred_sdk/feature.py`, after the manifest models and before the `@tool` decorator section:

```python
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
```

Note: `BaseFeature` must be defined *before* `_extract_tool_meta` and `tool` in the file, since they don't depend on it, but the class uses `_extract_tool_meta`. Place `BaseFeature` after the Pydantic models and the `_parse_google_docstring_args` and `_extract_tool_meta` functions, but before the `tool` decorator.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_feature.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/tests/test_feature.py
git commit -m "feat(sdk): add BaseFeature base class with auto-discovery of @tool methods"
```

### Task 3: Docstring Parsing Edge Cases

**Files:**
- Modify: `sdk/alfred_sdk/tests/test_feature.py`

- [ ] **Step 1: Write edge-case tests for docstring parser**

Append to `sdk/alfred_sdk/tests/test_feature.py`:

```python
from alfred_sdk.feature import _parse_google_docstring_args


def test_parse_google_docstring_basic() -> None:
    doc = """Do something.

    Args:
        room: The room name.
        level: Brightness 0-100.
    """
    args = _parse_google_docstring_args(doc)
    assert args["room"] == "The room name."
    assert args["level"] == "Brightness 0-100."


def test_parse_google_docstring_multiline_desc() -> None:
    doc = """Do something.

    Args:
        room: The room name, which can be
            a multi-line description.
        level: Brightness.
    """
    args = _parse_google_docstring_args(doc)
    assert args["room"] == "The room name, which can be a multi-line description."
    assert args["level"] == "Brightness."


def test_parse_google_docstring_no_args_section() -> None:
    doc = """Do something without args."""
    args = _parse_google_docstring_args(doc)
    assert args == {}


def test_parse_google_docstring_empty() -> None:
    args = _parse_google_docstring_args("")
    assert args == {}


def test_parse_google_docstring_args_then_returns() -> None:
    doc = """Do something.

    Args:
        x: The input.

    Returns:
        The output.
    """
    args = _parse_google_docstring_args(doc)
    assert args == {"x": "The input."}


def test_tool_meta_complex_types() -> None:
    """Complex type hints use str() representation."""

    class _ComplexFeature(BaseFeature):
        feature_name = "complex"

        @tool
        def do_thing(self, data: dict[str, Any], items: list[str]) -> dict:
            """Process data.

            Args:
                data: Input data mapping.
                items: List of items.
            """
            return {}

    feature = _ComplexFeature()
    tools = {t.name: t for t in feature.get_tools()}
    t = tools["complex.do_thing"]
    # Complex types use str() representation
    assert "dict" in t.parameters["data"].type
    assert "list" in t.parameters["items"].type


def test_tool_meta_default_values() -> None:
    """Default parameter values are captured."""

    class _DefaultFeature(BaseFeature):
        feature_name = "defaults"

        @tool
        def do_thing(self, x: int, y: int = 42) -> dict:
            """Process.

            Args:
                x: Required param.
                y: Optional param.
            """
            return {}

    feature = _DefaultFeature()
    tools = {t.name: t for t in feature.get_tools()}
    t = tools["defaults.do_thing"]
    assert t.parameters["x"].default is None  # No default
    assert t.parameters["y"].default == 42


def test_tool_name_override_in_get_tools() -> None:
    """@tool(name=...) overrides the qualified name in get_tools()."""

    class _OverrideFeature(BaseFeature):
        feature_name = "over"

        @tool(name="custom.my_tool")
        def do_thing(self, x: int) -> dict:
            """Do it."""
            return {}

    feature = _OverrideFeature()
    tools = feature.get_tools()
    assert tools[0].name == "custom.my_tool"
```

- [ ] **Step 2: Run tests to verify they pass (parser already implemented)**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_feature.py -v`
Expected: All PASS. If any fail, fix the parser in feature.py.

- [ ] **Step 3: Commit**

```bash
git add sdk/alfred_sdk/tests/test_feature.py
git commit -m "test(sdk): add edge-case tests for Google docstring parser"
```

---

## Chunk 2: SDK — `AlfredClient` Integration

### Task 4: `discover_features()` on AlfredClient

**Files:**
- Modify: `sdk/alfred_sdk/client.py`
- Create: `sdk/alfred_sdk/tests/test_client_features.py`

- [ ] **Step 1: Write failing tests**

Create `sdk/alfred_sdk/tests/test_client_features.py`:

```python
"""Tests for AlfredClient feature discovery and dispatch."""

from __future__ import annotations

import pytest

from alfred_sdk.client import AlfredClient
from alfred_sdk.feature import BaseFeature, tool


class _StubContext:
    def __init__(self) -> None:
        self.call_log: list[str] = []


class _AlphaFeature(BaseFeature):
    """Alpha feature for testing."""

    feature_name = "alpha"

    def __init__(self, ctx: _StubContext) -> None:
        super().__init__()
        self.ctx = ctx

    @tool
    def do_alpha(self, x: int) -> dict:
        """Do alpha thing.

        Args:
            x: The input value.
        """
        self.ctx.call_log.append(f"alpha:{x}")
        return {"x": x}


class _BetaFeature(BaseFeature):
    """Beta feature for testing."""

    feature_name = "beta"

    def __init__(self, ctx: _StubContext) -> None:
        super().__init__()
        self.ctx = ctx

    @tool
    def do_beta(self, y: str) -> dict:
        """Do beta thing.

        Args:
            y: The input string.
        """
        self.ctx.call_log.append(f"beta:{y}")
        return {"y": y}


def test_discover_features_from_classes() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    features = client.discover_features_from_classes(
        [_AlphaFeature, _BetaFeature], ctx=ctx
    )
    assert len(features) == 2
    # Tools are registered in dispatch table
    assert "alpha.do_alpha" in client._tool_fns
    assert "beta.do_beta" in client._tool_fns


def test_discover_features_dispatch() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    result = client.dispatch_sync("alpha.do_alpha", {"x": 42})
    assert result == {"x": 42}
    assert ctx.call_log == ["alpha:42"]


def test_discover_features_builds_manifests() -> None:
    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    manifest = client.get_registration_manifest()
    assert len(manifest["features"]) == 1
    assert manifest["features"][0]["name"] == "alpha"
    assert len(manifest["features"][0]["tools"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_client_features.py -v`
Expected: FAIL — `AlfredClient` has no `discover_features_from_classes` method

- [ ] **Step 3: Implement `discover_features_from_classes` and update `get_registration_manifest`**

In `sdk/alfred_sdk/client.py`, add these imports at the top:

```python
import importlib
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType
```

Add imports and the `_features` list to `__init__`:

```python
# Add to imports at top of file:
from __future__ import annotations
import importlib
import pkgutil
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from types import ModuleType
    from .feature import BaseFeature

# Add to __init__:
self._features: list[BaseFeature] = []
```

Add these methods to `AlfredClient`:

```python
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
        instance = cls(ctx) if ctx is not None else cls()
        instances.append(instance)
        # Register tool methods in dispatch table
        for tool_meta in instance.get_tools():
            # Find the bound method matching the tool's unqualified name
            method_name = tool_meta.name.split(".")[-1]
            bound_method = getattr(instance, method_name)
            if tool_meta.name in self._tool_fns:
                import logging
                logging.getLogger(__name__).warning(
                    "Tool name collision: '%s' — later registration wins", tool_meta.name
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

    if isinstance(package, str):
        pkg_module = importlib.import_module(package)
    else:
        pkg_module = package

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

def dispatch_sync(self, method: str, params: dict[str, Any]) -> Any:
    """Synchronous dispatch — for testing. Raises KeyError if not found."""
    fn = self._tool_fns.get(method)
    if fn is None:
        raise KeyError(f"Unknown tool: {method}")
    return fn(**params)
```

Update `get_registration_manifest()`:

```python
def get_registration_manifest(self) -> dict[str, Any]:
    """Build the tool registration manifest for Alfred's registry."""
    from .feature import BaseFeature

    feature_manifests: list[dict[str, Any]] = []
    for f in self._features:
        if isinstance(f, BaseFeature):
            feature_manifests.append(f.to_manifest().model_dump())

    return {
        "service_name": self.service_name,
        "service_endpoint": self.service_endpoint,
        "features": feature_manifests,
        "tools": self.tools,  # Legacy @client.tool() entries
        "publishers": [p["topic"] for p in self.publishers],
        "subscribers": [s["topic"] for s in self.subscribers],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_client_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/client.py sdk/alfred_sdk/tests/test_client_features.py
git commit -m "feat(sdk): add discover_features() and feature-aware manifest building"
```

### Task 5: `unregister()` on AlfredClient

**Files:**
- Modify: `sdk/alfred_sdk/client.py`
- Modify: `sdk/alfred_sdk/tests/test_client_features.py`

- [ ] **Step 1: Write failing test**

Append to `sdk/alfred_sdk/tests/test_client_features.py`:

```python
def test_discover_features_name_collision_warns(caplog: pytest.LogCaptureFixture) -> None:
    """Name collision between features logs a warning."""

    class _DuplicateFeature(BaseFeature):
        feature_name = "alpha"  # Same as _AlphaFeature

        def __init__(self, ctx: _StubContext) -> None:
            super().__init__()

        @tool
        def do_alpha(self, x: int) -> dict:
            """Duplicate."""
            return {"x": x}

    client = AlfredClient(service_name="test-svc")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature, _DuplicateFeature], ctx=ctx)
    assert "collision" in caplog.text.lower() or "alpha.do_alpha" in caplog.text


@pytest.mark.asyncio
async def test_register_includes_features_in_manifest() -> None:
    from unittest.mock import AsyncMock, patch

    client = AlfredClient(service_name="test-svc", redis_url="redis://fake:6379")
    ctx = _StubContext()
    client.discover_features_from_classes([_AlphaFeature], ctx=ctx)

    mock_redis = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    # Verify HSET was called with a manifest containing features
    call_args = mock_redis.hset.call_args
    import json
    manifest = json.loads(call_args[0][2])
    assert len(manifest["features"]) == 1
    assert manifest["features"][0]["name"] == "alpha"
    assert len(manifest["features"][0]["tools"]) == 1


@pytest.mark.asyncio
async def test_unregister_calls_hdel() -> None:
    from unittest.mock import AsyncMock, patch

    client = AlfredClient(service_name="test-svc", redis_url="redis://fake:6379")

    mock_redis = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.unregister()

    mock_redis.hdel.assert_called_once_with("alfred:tool_registry", "test-svc")
    mock_redis.aclose.assert_called_once()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_client_features.py::test_unregister_calls_hdel -v`
Expected: FAIL — `AlfredClient` has no `unregister` method

- [ ] **Step 3: Implement `unregister()`**

Add to `AlfredClient` in `sdk/alfred_sdk/client.py`:

```python
async def unregister(self) -> None:
    """Remove this service from Alfred's tool registry on Redis.

    Idempotent — safe to call if already unregistered.
    """
    import redis.asyncio as aioredis

    r: aioredis.Redis[Any] = aioredis.from_url(self.redis_url)  # type: ignore[type-arg]
    await r.hdel("alfred:tool_registry", self.service_name)
    await r.aclose()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/alfred_sdk/tests/test_client_features.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/client.py sdk/alfred_sdk/tests/test_client_features.py
git commit -m "feat(sdk): add AlfredClient.unregister() for graceful shutdown"
```

### Task 6: Update SDK Exports

**Files:**
- Modify: `sdk/alfred_sdk/__init__.py`

- [ ] **Step 1: Update exports**

Replace contents of `sdk/alfred_sdk/__init__.py`:

```python
"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .feature import BaseFeature, tool
from .mcp import mcp_tool
from .telemetry import track_event, track_latency, track_tokens

__all__ = [
    "AlfredClient",
    "BaseFeature",
    "mcp_tool",
    "tool",
    "track_event",
    "track_latency",
    "track_tokens",
]
```

- [ ] **Step 2: Verify imports work**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -c "from alfred_sdk import BaseFeature, tool; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Run full SDK test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest sdk/ -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add sdk/alfred_sdk/__init__.py
git commit -m "feat(sdk): export BaseFeature and tool from alfred_sdk"
```

---

## Chunk 3: Core — ToolRegistry and Reflex Engine

### Task 7: `ToolRegistry` and `ToolInfo`

**Files:**
- Create: `core/reflex/tool_registry.py`
- Create: `core/reflex/tests/test_tool_registry.py`

- [ ] **Step 1: Write failing tests**

Create `core/reflex/tests/test_tool_registry.py`:

```python
"""Tests for ToolRegistry — reads tool manifests from Redis."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from core.reflex.tool_registry import ToolInfo, ToolRegistry


def _make_manifest(service_name: str, features: list[dict]) -> str:
    """Build a JSON manifest string for testing."""
    return json.dumps(
        {
            "service_name": service_name,
            "service_endpoint": f"http://localhost:8000/mcp",
            "features": features,
            "tools": [],
        }
    )


LIGHTING_FEATURE = {
    "name": "lighting",
    "description": "Smart home lighting controls.",
    "tools": [
        {
            "name": "lighting.dim_lights",
            "description": "Dim the lights in a room.",
            "parameters": {
                "room": {"type": "str", "description": "The room to dim."},
                "level": {"type": "int", "description": "Brightness level 0-100."},
            },
        },
        {
            "name": "lighting.turn_off_lights",
            "description": "Turn off all lights in a room.",
            "parameters": {
                "room": {"type": "str", "description": "The room to turn off."},
            },
        },
    ],
}


@pytest.mark.asyncio
async def test_get_tools_parses_manifest() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert len(tools) == 2
    assert tools[0].name == "lighting.dim_lights"
    assert tools[0].target_service == "home-service"
    assert tools[0].feature_name == "lighting"
    assert tools[0].feature_description == "Smart home lighting controls."
    assert "room" in tools[0].parameters
    assert tools[0].parameters["room"]["type"] == "str"


@pytest.mark.asyncio
async def test_get_tools_empty_registry() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {}

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert tools == []


@pytest.mark.asyncio
async def test_get_tools_multiple_services() -> None:
    scenes_feature = {
        "name": "scenes",
        "description": "Scene management.",
        "tools": [
            {
                "name": "scenes.set_scene",
                "description": "Activate a scene.",
                "parameters": {"scene_name": {"type": "str", "description": "Scene name."}},
            }
        ],
    }
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
        b"other-service": _make_manifest("other-service", [scenes_feature]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert len(tools) == 3
    services = {t.target_service for t in tools}
    assert services == {"home-service", "other-service"}


@pytest.mark.asyncio
async def test_get_tools_legacy_tools_field() -> None:
    """ToolRegistry reads from legacy 'tools' field when 'features' is empty."""
    manifest = json.dumps(
        {
            "service_name": "legacy-svc",
            "service_endpoint": "http://localhost:9000/mcp",
            "features": [],
            "tools": [
                {
                    "name": "old_tool.do_thing",
                    "description": "A legacy tool.",
                    "parameters": {"x": {"type": "int"}},
                }
            ],
        }
    )
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {b"legacy-svc": manifest.encode()}

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    assert len(tools) == 1
    assert tools[0].name == "old_tool.do_thing"
    assert tools[0].feature_name == ""
    assert tools[0].target_service == "legacy-svc"


@pytest.mark.asyncio
async def test_get_tools_malformed_json_skipped() -> None:
    """Malformed JSON in a registry entry is skipped, not fatal."""
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"good-service": _make_manifest("good-service", [LIGHTING_FEATURE]).encode(),
        b"bad-service": b"not valid json {{{",
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()

    # Only tools from the good service are returned
    assert len(tools) == 2
    assert all(t.target_service == "good-service" for t in tools)


@pytest.mark.asyncio
async def test_get_services_returns_registered_services() -> None:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"home-service": _make_manifest("home-service", [LIGHTING_FEATURE]).encode(),
    }

    registry = ToolRegistry(mock_redis)
    tools = await registry.get_tools()
    services = registry.get_registered_services(tools)

    assert services == {"home-service"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/reflex/tests/test_tool_registry.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `ToolRegistry`**

Create `core/reflex/tool_registry.py`:

```python
"""ToolRegistry — reads tool manifests from Redis at runtime.

Thin read layer over the alfred:tool_registry Redis hash.
No caching — Redis HGETALL is sub-millisecond.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for async Redis
AioRedis = Any


@dataclass(frozen=True)
class ToolInfo:
    """A single tool discovered from the registry."""

    name: str
    description: str
    parameters: dict[str, dict[str, Any]]
    feature_name: str
    feature_description: str
    target_service: str


class ToolRegistry:
    """Reads tool manifests from Redis ``alfred:tool_registry``."""

    REGISTRY_KEY = "alfred:tool_registry"

    def __init__(self, redis: AioRedis) -> None:
        self._redis = redis

    async def get_tools(self) -> list[ToolInfo]:
        """Read all service manifests and return a flat list of tools."""
        raw: dict[bytes | str, bytes | str] = await self._redis.hgetall(self.REGISTRY_KEY)

        tools: list[ToolInfo] = []
        for service_key, manifest_json in raw.items():
            service_name = (
                service_key.decode() if isinstance(service_key, bytes) else service_key
            )
            manifest_str = (
                manifest_json.decode() if isinstance(manifest_json, bytes) else manifest_json
            )

            try:
                manifest: dict[str, Any] = json.loads(manifest_str)
            except json.JSONDecodeError:
                logger.error("Invalid JSON in registry for service '%s'", service_name)
                continue

            # Parse features
            for feature in manifest.get("features", []):
                feature_name = feature.get("name", "")
                feature_desc = feature.get("description", "")
                for t in feature.get("tools", []):
                    tools.append(
                        ToolInfo(
                            name=t["name"],
                            description=t.get("description", ""),
                            parameters=t.get("parameters", {}),
                            feature_name=feature_name,
                            feature_description=feature_desc,
                            target_service=service_name,
                        )
                    )

            # Parse legacy top-level tools
            for t in manifest.get("tools", []):
                tools.append(
                    ToolInfo(
                        name=t["name"],
                        description=t.get("description", ""),
                        parameters=t.get("parameters", {}),
                        feature_name="",
                        feature_description="",
                        target_service=service_name,
                    )
                )

        return tools

    @staticmethod
    def get_registered_services(tools: list[ToolInfo]) -> set[str]:
        """Extract the set of service names from a tool list."""
        return {t.target_service for t in tools}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/reflex/tests/test_tool_registry.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/reflex/tool_registry.py core/reflex/tests/test_tool_registry.py
git commit -m "feat(core): add ToolRegistry for reading tool manifests from Redis"
```

### Task 8: Reflex Engine Dynamic Prompt

**Files:**
- Modify: `core/reflex/engine.py`
- Modify: `core/reflex/tests/test_engine.py`

- [ ] **Step 1: Write failing tests for new engine API**

Replace `core/reflex/tests/test_engine.py` entirely:

```python
"""Tests for the Reflex Engine — System 1 SLM inference loop."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from bus.schemas.events import StateChangedEvent
from core.reflex.tool_registry import ToolInfo


def _make_tools() -> list[ToolInfo]:
    """Build a standard test tool list."""
    return [
        ToolInfo(
            name="lighting.dim_lights",
            description="Dim the lights in a room.",
            parameters={
                "room": {"type": "str", "description": "The room to dim."},
                "level": {"type": "int", "description": "Brightness level 0-100."},
            },
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
        ),
        ToolInfo(
            name="lighting.turn_off_lights",
            description="Turn off all lights in a room.",
            parameters={
                "room": {"type": "str", "description": "The room to turn off."},
            },
            feature_name="lighting",
            feature_description="Smart home lighting controls.",
            target_service="home-service",
        ),
    ]


@pytest.fixture
def mock_registry() -> AsyncMock:
    registry = AsyncMock()
    registry.get_tools = AsyncMock(return_value=_make_tools())
    return registry


@pytest.fixture
def mock_preferences() -> str:
    return (
        "# Lighting Preferences\n\n"
        "- I prefer dim lighting when watching TV or movies\n"
        "- Default brightness during daytime: 80%\n"
    )


@pytest.mark.asyncio
async def test_reflex_engine_produces_action(
    tv_on_event: StateChangedEvent,
    mock_preferences: str,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "home-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
        "prompt_tokens": 200,
        "completion_tokens": 30,
        "total_tokens": 230,
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ),
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
        )
        action = await engine.process_event(tv_on_event)

    assert action is not None
    assert action.tool_name == "lighting.dim_lights"
    assert action.parameters["level"] == 20
    assert action.target_service == "home-service"


@pytest.mark.asyncio
async def test_reflex_engine_returns_none_for_no_action(
    mock_preferences: str,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    boring_event = StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="sensor.temperature",
        new_state="22.5",
    )

    mock_ollama_response = {
        "response": json.dumps({"action": "none"}),
        "prompt_tokens": 150,
        "completion_tokens": 10,
        "total_tokens": 160,
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ),
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
        )
        action = await engine.process_event(boring_event)

    assert action is None


@pytest.mark.asyncio
async def test_reflex_engine_rejects_unknown_service(
    tv_on_event: StateChangedEvent,
    mock_preferences: str,
    mock_registry: AsyncMock,
) -> None:
    from core.reflex.engine import ReflexEngine

    mock_ollama_response = {
        "response": json.dumps(
            {
                "tool_name": "lighting.dim_lights",
                "target_service": "rogue-service",
                "parameters": {"room": "living_room", "level": 20},
            }
        ),
    }

    with (
        patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences),
        patch(
            "core.reflex.ollama_client.infer",
            new_callable=AsyncMock,
            return_value=mock_ollama_response,
        ),
    ):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
        )
        action = await engine.process_event(tv_on_event)

    assert action is None


@pytest.mark.asyncio
async def test_reflex_engine_prompt_contains_tools(
    mock_registry: AsyncMock,
    mock_preferences: str,
) -> None:
    from core.reflex.engine import ReflexEngine

    with patch("core.reflex.memory_reader.read_preferences", return_value=mock_preferences):
        engine = ReflexEngine(
            preferences_dir="/fake/prefs",
            tool_registry=mock_registry,
        )

    tools = _make_tools()
    prompt = engine._build_system_prompt(tools)

    assert "lighting.dim_lights" in prompt
    assert "lighting.turn_off_lights" in prompt
    assert "home-service" in prompt
    assert "Smart home lighting controls." in prompt


@pytest.mark.asyncio
async def test_reflex_engine_prompt_legacy_tools() -> None:
    """Legacy tools (empty feature_name) render with service header."""
    from core.reflex.engine import _build_tool_section
    from core.reflex.tool_registry import ToolInfo

    legacy_tools = [
        ToolInfo(
            name="old_tool.do_thing",
            description="A legacy tool.",
            parameters={"x": {"type": "int"}},
            feature_name="",
            feature_description="",
            target_service="legacy-svc",
        ),
    ]

    section = _build_tool_section(legacy_tools)
    assert "legacy-svc" in section
    assert "legacy tools" in section.lower()
    assert "old_tool.do_thing" in section
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/reflex/tests/test_engine.py -v`
Expected: FAIL — `ReflexEngine` doesn't accept `tool_registry`

- [ ] **Step 3: Rewrite `core/reflex/engine.py`**

Replace `core/reflex/engine.py` entirely:

```python
"""Reflex Engine — System 1 fast-path SLM inference.

Consumes events from Redis Streams, reads Markdown preferences,
prompts the local SLM, and produces structured ActionRequests.

Design for eval-ability: structured (event, preferences) in → structured action out.
No side effects during inference.
"""

from __future__ import annotations

import json
import logging

from bus.schemas.events import ActionRequest, StateChangedEvent
from core.reflex import ollama_client
from core.reflex.memory_reader import read_preferences
from core.reflex.tool_registry import ToolInfo, ToolRegistry
from sdk.alfred_sdk.telemetry import track_latency

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """You are Alfred's Reflex Engine — a fast-acting steward for a smart home.

Given an event from the smart home and the user's preferences, decide if an action is needed.

Rules:
- Only act if the event clearly matches a user preference
- If no action is needed, respond with: {{"action": "none"}}
- If an action IS needed, respond with:
  {{"tool_name": "<tool>", "target_service": "<service>", "parameters": {{<params>}}}}

{tool_section}

Respond ONLY with valid JSON. No explanation."""


def _build_tool_section(tools: list[ToolInfo]) -> str:
    """Build the 'Available tools' section of the system prompt."""
    if not tools:
        return "No tools available."

    # Group tools by (feature_name, target_service)
    groups: dict[tuple[str, str], list[ToolInfo]] = {}
    for t in tools:
        key = (t.feature_name, t.target_service)
        groups.setdefault(key, []).append(t)

    lines: list[str] = ["Available tools:"]
    for (feature_name, service), group_tools in groups.items():
        feature_desc = group_tools[0].feature_description
        if feature_name:
            header = f"\n## {feature_name} [{service}]"
            if feature_desc:
                header += f" — {feature_desc}"
            lines.append(header)
        else:
            lines.append(f"\n## {service} (legacy tools)")

        for t in group_tools:
            params_str = ", ".join(
                f"{p}: {info.get('type', 'Any')}" for p, info in t.parameters.items()
            )
            line = f"- {t.name}({params_str})"
            if t.description:
                line += f" — {t.description}"
            lines.append(line)

    return "\n".join(lines)


class ReflexEngine:
    """The System 1 fast-path inference engine."""

    def __init__(self, preferences_dir: str, tool_registry: ToolRegistry) -> None:
        self.preferences_dir = preferences_dir
        self._registry = tool_registry
        self._cached_preferences: str | None = None

    def _get_preferences(self) -> str:
        """Return cached preferences, loading from disk on first call."""
        if self._cached_preferences is None:
            self._cached_preferences = read_preferences(self.preferences_dir)
        return self._cached_preferences

    def _build_system_prompt(self, tools: list[ToolInfo]) -> str:
        """Build the system prompt with dynamically discovered tools."""
        tool_section = _build_tool_section(tools)
        return _SYSTEM_PROMPT_TEMPLATE.format(tool_section=tool_section)

    @track_latency(category="reflex")
    async def process_event(self, event: StateChangedEvent) -> ActionRequest | None:
        """Process a state change event and optionally produce an action."""
        preferences = self._get_preferences()
        tools = await self._registry.get_tools()
        valid_services = ToolRegistry.get_registered_services(tools)

        prompt = (
            f"{self._build_system_prompt(tools)}\n\n"
            f"## User Preferences\n{preferences}\n\n"
            f"## Event\n"
            f"Entity: {event.entity_id}\n"
            f"Domain: {event.domain}\n"
            f"Changed: {event.old_state} → {event.new_state}\n"
            f"Attributes: {json.dumps(event.attributes)}\n\n"
            f"## Your Decision (JSON only):"
        )

        response = await ollama_client.infer(prompt)
        return self._parse_response(response, event, valid_services)

    def _parse_response(
        self,
        response: dict[str, object],
        event: StateChangedEvent,
        valid_services: set[str],
    ) -> ActionRequest | None:
        """Parse the SLM's JSON response into an ActionRequest or None."""
        try:
            raw = response.get("response", "")
            parsed = json.loads(str(raw))

            if parsed.get("action") == "none":
                logger.debug("No action for event %s", event.entity_id)
                return None

            tool_name = parsed.get("tool_name")
            if not tool_name:
                logger.warning("SLM response missing tool_name: %s", raw)
                return None

            target_service = str(parsed.get("target_service", ""))
            if target_service not in valid_services:
                logger.warning(
                    "SLM returned unregistered target_service: %s (valid: %s)",
                    target_service,
                    valid_services,
                )
                return None

            return ActionRequest(
                source="reflex-engine",
                target_service=target_service,
                tool_name=str(tool_name),
                parameters=dict(parsed.get("parameters", {})),
            )
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse SLM response: %s — %s", e, response)
            return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/reflex/tests/test_engine.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add core/reflex/engine.py core/reflex/tests/test_engine.py
git commit -m "feat(core): dynamic tool prompt in Reflex Engine via ToolRegistry"
```

### Task 9: Wire ToolRegistry into `__main__.py`

**Files:**
- Modify: `core/reflex/__main__.py`

- [ ] **Step 1: Update `__main__.py`**

Replace `core/reflex/__main__.py`:

```python
"""Entry point for the Reflex Runner service.

Usage: python -m core.reflex
"""

from __future__ import annotations

import asyncio
import logging
import signal

import redis.asyncio as aioredis

from core.memory.scratchpad_writer import ScratchpadWriter
from core.reflex.engine import ReflexEngine
from core.reflex.runner import AioRedis, ensure_consumer_group, process_stream_entry
from core.reflex.tool_registry import ToolRegistry
from domains.home.home_agent import HomeAgent
from sdk.alfred_sdk.telemetry import clear_telemetry_buffer, get_telemetry_buffer
from shared.config import AlfredConfig
from telemetry.collector import flush_to_csv

logger = logging.getLogger(__name__)

STREAM = "alfred:home:state_changed"
GROUP = "reflex-engine"
CONSUMER = "worker-1"
RESULT_STREAM = "alfred:home:action_results"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    logger.info("Shutdown signal received")
    _shutdown.set()


async def flush_telemetry_periodically(config: AlfredConfig, interval: float = 30.0) -> None:
    """Periodically flush the telemetry buffer to CSV."""
    while True:
        await asyncio.sleep(interval)
        buf = get_telemetry_buffer()
        if buf:
            entries = list(buf)
            clear_telemetry_buffer()
            flush_to_csv(entries, config.research_vault_path)
            logger.info("Flushed %d telemetry entries", len(entries))


async def run(config: AlfredConfig) -> None:
    """Main Reflex Runner event loop."""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _handle_signal)

    r: AioRedis = aioredis.from_url(config.redis_url)

    await ensure_consumer_group(r, STREAM, GROUP)

    # Fail-fast: verify tools are registered before entering the event loop
    registry = ToolRegistry(r)
    tools = await registry.get_tools()
    if not tools:
        await r.aclose()
        raise RuntimeError(
            "No tools found in alfred:tool_registry. "
            "Start at least one microservice (e.g., home-service) before the Reflex Runner."
        )
    logger.info("Loaded %d tools from %d services", len(tools), len(ToolRegistry.get_registered_services(tools)))

    engine = ReflexEngine(preferences_dir="core/memory/preferences", tool_registry=registry)
    agent = HomeAgent(redis=r)
    writer = ScratchpadWriter(redis=r, queue_key=SCRATCHPAD_QUEUE)

    # Background tasks
    scratchpad_task = asyncio.create_task(writer.run())
    telemetry_task = asyncio.create_task(flush_telemetry_periodically(config))

    logger.info("Reflex Runner started. Listening on stream '%s'...", STREAM)

    try:
        while not _shutdown.is_set():
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=10, block=5000)

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    try:
                        await process_stream_entry(
                            entry_data=entry_data,
                            engine=engine,
                            agent=agent,
                            redis=r,
                            result_stream=RESULT_STREAM,
                            scratchpad_queue=SCRATCHPAD_QUEUE,
                        )
                        await r.xack(STREAM, GROUP, entry_id)
                    except Exception as e:
                        logger.error("Error processing entry %s: %s — will retry", entry_id, e)
    finally:
        logger.info("Shutting down Reflex Runner...")
        scratchpad_task.cancel()
        telemetry_task.cancel()
        await r.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    config = AlfredConfig.from_env()
    asyncio.run(run(config))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run existing tests to verify nothing is broken**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest core/reflex/tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add core/reflex/__main__.py
git commit -m "feat(core): wire ToolRegistry into Reflex Runner with fail-fast startup"
```

---

## Chunk 4: Home-Service Migration and Documentation

### Task 10: Migrate home-service to BaseFeature

**Files:**
- Create: `home-service/alfred_ext/features/__init__.py`
- Create: `home-service/alfred_ext/features/lighting.py`
- Create: `home-service/alfred_ext/features/scenes.py`
- Modify: `home-service/alfred_ext/register.py`
- Modify: `home-service/app/server.py` (add unregister on shutdown)
- Modify: `home-service/tests/test_server.py`

- [ ] **Step 1: Create feature package and feature files**

Create `home-service/alfred_ext/features/__init__.py` (empty file).

Create `home-service/alfred_ext/features/lighting.py`:

```python
"""Lighting feature — controls smart home lights via Home Assistant."""

from __future__ import annotations

from typing import Any

from alfred_sdk import BaseFeature, tool


class LightingFeature(BaseFeature):
    """Smart home lighting controls."""

    feature_name = "lighting"

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ha = ctx.ha

    @tool
    async def dim_lights(self, room: str, level: int) -> dict:
        """Dim the lights in a room.

        Args:
            room: The room to dim.
            level: Brightness level 0-100.
        """
        entity_id = f"light.{room}"
        brightness = int(level * 2.55)  # Convert 0-100 to 0-255
        await self.ha.call_service(
            "light", "turn_on", {"entity_id": entity_id, "brightness": brightness}
        )
        return {"entity_id": entity_id, "brightness": level}

    @tool
    async def turn_off_lights(self, room: str) -> dict:
        """Turn off all lights in a room.

        Args:
            room: The room to turn off.
        """
        entity_id = f"light.{room}"
        await self.ha.call_service("light", "turn_off", {"entity_id": entity_id})
        return {"entity_id": entity_id, "state": "off"}
```

Create `home-service/alfred_ext/features/scenes.py`:

```python
"""Scene feature — activates Home Assistant scenes."""

from __future__ import annotations

from typing import Any

from alfred_sdk import BaseFeature, tool


class SceneFeature(BaseFeature):
    """Smart home scene management."""

    feature_name = "scenes"

    def __init__(self, ctx: Any) -> None:
        super().__init__()
        self.ha = ctx.ha

    @tool
    async def set_scene(self, scene_name: str) -> dict:
        """Activate a Home Assistant scene.

        Args:
            scene_name: The scene to activate.
        """
        entity_id = f"scene.{scene_name}"
        await self.ha.call_service("scene", "turn_on", {"entity_id": entity_id})
        return {"scene": scene_name, "activated": True}
```

- [ ] **Step 2: Rewrite `home-service/alfred_ext/register.py`**

```python
"""Alfred integration for home-service.

Optional — this module is only used when alfred-sdk is installed.
The home-service works independently without it.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from alfred_sdk import AlfredClient

from app.ha_client import HomeAssistantClient

ha = HomeAssistantClient(
    host=os.getenv("HA_HOST", "http://homeassistant.local:8123"),
    token=os.getenv("HA_TOKEN", ""),
)

client = AlfredClient(
    service_name="home-service",
    service_endpoint=f"http://{os.getenv('SERVICE_HOST', 'localhost')}:8000/mcp",
)


class HomeServiceContext:
    """Shared dependencies for all home-service features."""

    def __init__(self, ha: HomeAssistantClient) -> None:
        self.ha = ha


import alfred_ext.features as features_pkg

client.discover_features(
    package=features_pkg,
    ctx=HomeServiceContext(ha=ha),
)
```

- [ ] **Step 3: Update server.py for graceful unregister**

In `home-service/app/server.py`, update the lifespan to unregister on shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    """Register tools with Alfred on startup, unregister on shutdown."""
    try:
        from alfred_ext.register import client

        await client.register()
        logger.info("Registered tools with Alfred registry")
    except Exception as e:
        logger.warning("Could not register with Alfred: %s", e)
    yield
    try:
        from alfred_ext.register import client

        await client.unregister()
        logger.info("Unregistered from Alfred registry")
    except Exception as e:
        logger.warning("Could not unregister from Alfred: %s", e)
```

- [ ] **Step 4: Update test_server.py with new tool names**

In `home-service/tests/test_server.py`, update the tool call test:

Change `"method": "smart_home.dim_lights"` to `"method": "lighting.dim_lights"` in `test_mcp_endpoint_dispatches_tool_call`.

- [ ] **Step 5: Run home-service tests**

Run: `cd /Users/anirudhlath/code/private/alfred/home-service && python -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred
git add home-service/alfred_ext/features/ home-service/alfred_ext/register.py home-service/app/server.py home-service/tests/test_server.py
git commit -m "feat(home-service): migrate from @client.tool() to BaseFeature pattern"
```

### Task 11: Update Documentation

**Files:**
- Modify: `alfred/sdk/CLAUDE.md`
- Modify: `alfred/core/CLAUDE.md`
- Modify: `alfred/.claude/rules/sdk/sdk-design.md`
- Modify: `alfred/.claude/rules/core/reflex-engine.md`

- [ ] **Step 1: Update `sdk/CLAUDE.md`**

Replace with:

```markdown
# alfred-sdk

Publishable Python package. The ONLY coupling between Alfred and external apps.

- Must work standalone — no imports from alfred core, bus, or domains
- Keep dependencies minimal
- Core: AlfredClient, BaseFeature, @tool, @mcp_tool (legacy), telemetry decorators
- BaseFeature + @tool is the recommended way to define tools — auto-extracts metadata from docstrings + type hints
- AlfredClient.discover_features() scans a package for BaseFeature subclasses and registers their tools
- @mcp_tool / @client.tool() still work for unmigrated services (legacy)
- AlfredClient.dispatch() handles both BaseFeature methods and legacy tool functions
- Not published to PyPI — container builds install from source path
```

- [ ] **Step 2: Update `core/CLAUDE.md`**

Replace with:

```markdown
# Core — Alfred OS

This directory contains Alfred's brain:
- `reflex/` — System 1 SLM engine (fast event → action loop)
  - `engine.py` — SLM inference with dynamic tool prompt
  - `tool_registry.py` — Reads tool manifests from Redis `alfred:tool_registry`
  - `runner.py` — Event loop orchestration
- `memory/` — Markdown preferences + scratchpad
- `triggers/` — Dynamic trigger engine (Phase 2)
- `conscious/` — System 2 cloud LLM (Phase 3)
- `voice/` — Voice I/O adapters (Phase 3)
- `librarian/` — Nightly preference consolidation (Phase 3)

See path-scoped rules in .claude/rules/core/ for component-specific constraints.
```

- [ ] **Step 3: Update `.claude/rules/sdk/sdk-design.md`**

Replace with:

```markdown
---
paths:
  - "sdk/**"
---

# SDK Design Rules

- alfred-sdk is a publishable Python package — keep dependencies minimal
- It is the ONLY coupling between Alfred and external apps
- Core exports: AlfredClient, BaseFeature, @tool, telemetry decorators
- BaseFeature + @tool is the standard pattern for defining tools — auto-extracts names, descriptions, and parameters from Python code
- AlfredClient.discover_features() scans a package for BaseFeature subclasses and registers tools
- Legacy @mcp_tool and @client.tool() still work but should not be used for new tools
- Apps install it as an optional dependency
- The SDK must work standalone — no imports from alfred core, bus, or domains
- Registration via client.register() announces feature manifests to Redis registry
- Unregistration via client.unregister() on graceful shutdown (HDEL)
- MCP transport is HTTP (JSON-RPC) between networked containers
```

- [ ] **Step 4: Update `.claude/rules/core/reflex-engine.md`**

Replace with:

```markdown
---
paths:
  - "core/reflex/**"
---

# Reflex Engine Rules

The Reflex Engine (System 1) is the fast-path SLM that processes events.

- MUST be eval-able: structured (event, preferences) in → structured action out
- No side effects during inference — side effects happen when the action is executed
- Reads preferences from core/memory/preferences/ (read-only)
- Reads tools from ToolRegistry (Redis `alfred:tool_registry`) — NEVER hardcode tool names
- Builds system prompt dynamically from registered tool metadata
- Validates SLM-returned target_service against registered services
- Appends observations to scratchpad via Redis List (never direct file write)
- Target latency: sub-500ms event → action
- All inference calls MUST use @track_latency and @track_tokens decorators
- Never call the cloud LLM (System 2) from the reflex path
- Uses Ollama for local inference — model configured via OLLAMA_MODEL env var
- Fail-fast at startup if no tools are registered
```

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
git add sdk/CLAUDE.md core/CLAUDE.md .claude/rules/sdk/sdk-design.md .claude/rules/core/reflex-engine.md
git commit -m "docs: update CLAUDE.md and rules for BaseFeature and ToolRegistry"
```

### Task 12: Run Full Test Suite and Lint

- [ ] **Step 1: Run ruff**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
ruff check . --fix
ruff format .
```

- [ ] **Step 2: Run mypy**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
mypy bus/ core/ domains/ sdk/ shared/ telemetry/
```

Fix any type errors that arise.

- [ ] **Step 3: Run full test suite**

```bash
cd /Users/anirudhlath/code/private/alfred/alfred
python -m pytest -v
```

- [ ] **Step 4: Commit any fixes**

```bash
# Stage only the files modified by lint/type fixes (review before staging)
git diff --name-only
# Then: git add <specific files>
git commit -m "fix: resolve lint, type, and test issues from BaseFeature migration"
```
