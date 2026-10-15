# SDK Tool Input Schemas Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every SDK tool carries one real JSON Schema for its arguments (`input_schema`), with correct `required`, and System 2 offers that schema to the model unchanged.

**Architecture:** The SDK generates `input_schema` with Pydantic from each `@tool` method's signature, or assembles it from `ToolParameter`s for hand-built tools. The manifest carries it to Redis next to the legacy `parameters` map. Core's `ToolRegistry` reads it, deriving one from `parameters` for manifests written by an older SDK, and System 2's `_tools_to_openai_format` passes it through. The trigger feature stops dropping `audience`/`risk` (#314). A follow-up home-service PR marks its required parameters.

**Tech Stack:** Python 3.13, Pydantic v2 (`create_model`, `model_json_schema`), pytest, uv, `gh stack`.

**Spec:** `docs/superpowers/specs/2026-10-08-pydantic-ai-adoption-design.md`, component 1 ("SDK manifest: `input_schema`") and the Delivery table rows 1 and 1b.

## Global Constraints

- Python 3.13: run `uv venv --python 3.13` in a new worktree before `uv sync --all-extras`.
- The SDK must not import from `core`, `bus`, `domains` or `shared`. Its only dependencies stay `pydantic>=2.0` and `redis>=5.0`. Core may import from the SDK.
- The legacy `parameters` map stays on the wire, with `required` filled in: Reflex renders parameter names during its shadow week (#285). Do not change Reflex's prompt.
- One JSON Schema per tool (`input_schema`), not per parameter (owner's decision, 2026-10-08).
- `mypy --strict`, ruff at line length 100, Google docstrings, conventional commit messages.
- The repo is public. No personal data, IPs, hostnames, secrets, household names or Claude model identifiers in commits, PRs or issues. Before every push, run both greps in `~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md` §Secret hygiene; never quote them in a committed file.
- Merging to `master` deploys to production. Push, open PRs or edit issues only on the owner's go; merge only on the owner's explicit order.
- Never use bare `git stash`. Never run two full pytest suites at once.
- Every reviewer finding gets fixed, Minor included.

## Review Focus

- **A parameter named `title` or `default`** (a calendar tool's `title`): its schema survives title-stripping intact, and `default` values that contain a `"title"` key are not altered. Test: Task 2, `test_title_stripping_keeps_names_and_data`.
- **A manifest written by an older SDK** (any service not yet rebuilt, and the eval stack's home-service at `origin/main`): its tools are still offered, with exactly today's schema. Test: Task 4, `test_old_sdk_manifest_keeps_todays_schema`.
- **One malformed tool in a service's manifest**: that tool is skipped with a warning, and every other tool, from that service and from others, is still offered. Test: Task 4, `test_malformed_tool_is_skipped_and_the_rest_still_load`.
- **A parameter whose default JSON cannot represent** (a sentinel object): the tool still registers, the parameter is optional, the schema carries no default, and no warning leaks. Test: Task 2, `test_unserializable_default_stays_optional_without_warning`.
- **A critical tool offered twice** (two turns reuse one registry read): the injected `reason` never leaks into the registry's schema and is not added twice. Test: Task 5, `test_reason_injection_leaves_the_registry_schema_untouched`.

---

### Task 1: SDK data model — `input_schema` on manifests, assembled for hand-built tools

**Files:**
- Modify: `sdk/alfred_sdk/feature.py` (imports; `ToolParameter`; `ToolManifest`; `ToolMeta`; `BaseFeature.to_manifest`; new `JSON_TYPE_BY_PYTHON_NAME`, `input_schema_from_parameters`)
- Modify: `shared/type_map.py`
- Test: `sdk/alfred_sdk/tests/test_feature.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `sdk.alfred_sdk.feature.JSON_TYPE_BY_PYTHON_NAME: dict[str, str]` (`"str" → "string"`, `"int" → "integer"`, `"float" → "number"`, `"bool" → "boolean"`, `"dict" → "object"`, `"list" → "array"`).
  - `ToolParameter.required: bool = False`, `ToolParameter.json_schema: dict[str, Any] | None = None`.
  - `input_schema_from_parameters(parameters: Mapping[str, ToolParameter]) -> dict[str, Any]`: returns `{"type": "object", "properties": {...}, "required": [...]}`.
  - `ToolMeta.input_schema: dict[str, Any]` (default empty, filled from `parameters` in `__post_init__`).
  - `ToolManifest.input_schema: dict[str, Any]`, set by `to_manifest()`.
  - `shared.type_map.PYTHON_TO_JSON_SCHEMA` stays importable (an alias of the SDK map) until Task 5 removes its last user.

- [ ] **Step 0: Branch, stack and environment**

The worktree is `~/code/.worktrees/alfred/pydantic-ai-adoption`. Branch `feat/sdk-input-schema` already exists on top of `docs/pydantic-ai-adoption` and holds this plan.

```bash
W=~/code/.worktrees/alfred/pydantic-ai-adoption; cd "$W"
git branch --show-current          # expect feat/sdk-input-schema
gh stack init docs/pydantic-ai-adoption feat/sdk-input-schema   # adopts both, bottom to top
gh stack view
uv venv --python 3.13 && uv sync --all-extras
```

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `sdk/alfred_sdk/tests/test_feature.py`:

```python
import dataclasses
import json

from sdk.alfred_sdk.feature import (
    BaseFeature,
    ServiceManifest,
    ToolMeta,
    ToolParameter,
    _parse_google_docstring_args,
    input_schema_from_parameters,
    tool,
)
```

(Replace the existing `from sdk.alfred_sdk.feature import BaseFeature, _parse_google_docstring_args, tool` line with the block above.)

Append:

```python
# ── input_schema: assembled from ToolParameters (hand-built tools) ──


def test_hand_built_tool_meta_derives_input_schema() -> None:
    meta = ToolMeta(
        name="home.light_turn_on",
        description="Turn on a light.",
        parameters={
            "target": ToolParameter(type="str", description="Area or entity.", required=True),
            "brightness": ToolParameter(type="float", description="0-255."),
            "data": ToolParameter(type="dict"),
        },
    )
    assert meta.input_schema == {
        "type": "object",
        "properties": {
            "target": {"type": "string", "description": "Area or entity."},
            "brightness": {"type": "number", "description": "0-255."},
            "data": {"type": "object"},
        },
        "required": ["target"],
    }


def test_parameter_json_schema_overrides_the_type_name() -> None:
    schema = input_schema_from_parameters(
        {
            "mode": ToolParameter(
                type="str",
                description="Fan mode.",
                json_schema={"type": "string", "enum": ["low", "high"]},
            )
        }
    )
    assert schema["properties"]["mode"] == {
        "type": "string",
        "enum": ["low", "high"],
        "description": "Fan mode.",
    }


def test_legacy_type_names_map_like_before() -> None:
    schema = input_schema_from_parameters(
        {
            "when": ToolParameter(type="datetime.datetime | None"),
            "items": ToolParameter(type="list[str]"),
            "n": ToolParameter(type="int"),
            "flag": ToolParameter(type="bool"),
            "odd": ToolParameter(type="SomethingElse"),
        }
    )
    assert {name: prop["type"] for name, prop in schema["properties"].items()} == {
        "when": "string",
        "items": "array",
        "n": "integer",
        "flag": "boolean",
        "odd": "string",
    }


def test_zero_parameter_tool_has_an_empty_object_schema() -> None:
    meta = ToolMeta(name="x.ping", description="Ping.", parameters={})
    assert meta.input_schema == {"type": "object", "properties": {}, "required": []}


def test_explicit_input_schema_is_kept() -> None:
    explicit = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    meta = ToolMeta(name="x.q", description="Q.", parameters={}, input_schema=explicit)
    assert meta.input_schema == explicit


def test_replace_keeps_input_schema_audience_and_risk() -> None:
    meta = ToolMeta(
        name="x.q",
        description="Q.",
        parameters={"q": ToolParameter(type="str", required=True)},
        audience="reflex",
        risk="critical",
    )
    copy = dataclasses.replace(meta, description="Q, enriched.")
    assert copy.input_schema == meta.input_schema
    assert (copy.audience, copy.risk) == ("reflex", "critical")


class _HandBuiltFeature(BaseFeature):
    """Tools built without a signature, like home-service's."""

    feature_name = "hand"

    def get_tools(self) -> list[ToolMeta]:
        return [
            ToolMeta(
                name="hand.go",
                description="Go somewhere.",
                parameters={"to": ToolParameter(type="str", description="Where.", required=True)},
            )
        ]


def test_manifest_carries_input_schema_through_json() -> None:
    manifest = ServiceManifest(
        service_name="svc",
        service_endpoint="http://svc/mcp",
        features=[_HandBuiltFeature().to_manifest()],
    )
    wire = json.loads(json.dumps(manifest.model_dump()))
    tool_json = wire["features"][0]["tools"][0]
    assert tool_json["input_schema"] == {
        "type": "object",
        "properties": {"to": {"type": "string", "description": "Where."}},
        "required": ["to"],
    }
    # The legacy map stays on the wire, now saying which parameters are required.
    assert tool_json["parameters"]["to"]["required"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest sdk/alfred_sdk/tests/test_feature.py -q`
Expected: collection error, `ImportError: cannot import name 'input_schema_from_parameters'`.

- [ ] **Step 3: Implement**

In `sdk/alfred_sdk/feature.py`:

Change the imports to:

```python
import inspect
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar, overload

from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping
```

Replace `class ToolParameter` with:

```python
# Python type names, as `ToolParameter.type` carries them → JSON Schema types.
JSON_TYPE_BY_PYTHON_NAME: dict[str, str] = {
    "str": "string",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
    "dict": "object",
    "list": "array",
}


class ToolParameter(BaseModel):
    """Schema for a single tool parameter in the manifest.

    ``type`` is the legacy type name, kept on the wire for readers of ``parameters``
    (Reflex renders the names). A hand-built tool sets ``required`` and, for an exact
    schema, ``json_schema``; without it the schema is derived from ``type``.
    """

    type: str
    description: str = ""
    default: Any = None
    required: bool = False
    json_schema: dict[str, Any] | None = None
```

In `class ToolManifest`, add after `parameters`:

```python
    # The tool's arguments as one JSON Schema object: what a model is offered.
    input_schema: dict[str, Any] = {}
```

Replace the `ToolMeta` section (from `# ── ToolMeta dataclass ──` to the end of the class) with:

```python
# ── input_schema assembly (hand-built tools) ──


def _base_type_name(type_name: str) -> str:
    """``"list[str]"`` → ``"list"``, ``"datetime.datetime | None"`` → ``"datetime.datetime"``."""
    return type_name.split("|")[0].strip().split("[")[0].strip()


def input_schema_from_parameters(parameters: Mapping[str, ToolParameter]) -> dict[str, Any]:
    """Assemble a tool's JSON Schema from per-parameter metadata.

    For tools built by hand (no signature to read) and for manifests written before
    ``input_schema`` existed. A parameter's ``json_schema`` wins over its ``type`` name.

    Args:
        parameters: Parameter name → its metadata.

    Returns:
        An object schema with ``properties`` and ``required``.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in parameters.items():
        prop: dict[str, Any] = (
            dict(param.json_schema)
            if param.json_schema is not None
            else {"type": JSON_TYPE_BY_PYTHON_NAME.get(_base_type_name(param.type), "string")}
        )
        if param.description and "description" not in prop:
            prop["description"] = param.description
        properties[name] = prop
        if param.required:
            required.append(name)
    return {"type": "object", "properties": properties, "required": required}


# ── ToolMeta dataclass ──


@dataclass(frozen=True)
class ToolMeta:
    """Extracted metadata for a single tool method.

    ``input_schema`` is the tool's arguments as one JSON Schema object. Left empty, it
    is assembled from ``parameters``.
    """

    name: str
    description: str
    parameters: dict[str, ToolParameter]
    audience: ToolAudience = "conscious"
    risk: ToolRisk = "benign"
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_schema:
            # Frozen dataclass: object.__setattr__ is the sanctioned way to fill a derived field.
            object.__setattr__(
                self, "input_schema", input_schema_from_parameters(self.parameters)
            )
```

In `BaseFeature.to_manifest`, add `input_schema=t.input_schema,` after `risk=t.risk,` in the `ToolManifest(...)` call.

Replace the body of `shared/type_map.py` above `def friendly_type` so the map has one copy:

```python
"""Shared Python → JSON Schema type mapping."""

from __future__ import annotations

from typing import Any

from sdk.alfred_sdk.feature import JSON_TYPE_BY_PYTHON_NAME

# Alias kept for core/conscious/engine.py until it reads the SDK's input_schema.
PYTHON_TO_JSON_SCHEMA = JSON_TYPE_BY_PYTHON_NAME
```

and in `friendly_type`, change the last line to `return JSON_TYPE_BY_PYTHON_NAME.get(base, base)`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest sdk/alfred_sdk/tests/test_feature.py core/triggers/tests/ -q`
Expected: all pass (the trigger tests cover `friendly_type`).

- [ ] **Step 5: Commit**

```bash
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/tests/test_feature.py shared/type_map.py
git commit -m "feat(sdk): carry one input schema per tool in the manifest (#301)"
```

---

### Task 2: SDK — generate `input_schema` from `@tool` signatures with Pydantic

**Files:**
- Modify: `sdk/alfred_sdk/feature.py` (`_extract_tool_meta`; new `_strip_titles`, `_signature_input_schema`)
- Modify: `docs/sdk.md` ("What @tool extracts automatically", "Docstring format", "Manifest Format")
- Modify: `sdk/CLAUDE.md` (Gotchas)
- Test: `sdk/alfred_sdk/tests/test_feature.py`

**Interfaces:**
- Consumes: `ToolMeta(..., input_schema=...)`, `ToolParameter.required` (Task 1).
- Produces: for every `@tool` method, `ToolMeta.input_schema` is Pydantic's schema of its signature with `title` keywords removed, always containing `type`, `properties` and `required`; `ToolParameter.required` is `True` exactly when the parameter has no default. `_extract_tool_meta` raises `TypeError` naming the tool for `*args`, `**kwargs`, positional-only parameters, unresolvable type hints, and types Pydantic cannot describe.

- [ ] **Step 1: Write the failing tests**

Add to the imports of `sdk/alfred_sdk/tests/test_feature.py`:

```python
import datetime as _dt  # noqa: TC003 — get_type_hints and the pydantic model need it at runtime
import enum
import warnings
from typing import Literal

import pytest
from pydantic import BaseModel
```

Append (all feature classes are module-level so `get_type_hints` can resolve their annotations):

```python
# ── input_schema: generated from @tool signatures ──


class _Speed(enum.Enum):
    SLOW = "slow"
    FAST = "fast"


class _Window(BaseModel):
    start: _dt.datetime
    title: str = ""


class _ShapesFeature(BaseFeature):
    feature_name = "shapes"

    @tool
    def plan(
        self,
        title: str,
        tags: list[str],
        mode: Literal["a", "b"],
        speed: _Speed,
        at: _dt.datetime,
        window: _Window | None = None,
        limit: int = 5,
        default: str = "x",
        meta: dict[str, Any] | None = None,
        style: dict[str, str] = {"title": "bold"},  # noqa: B006 — data containing "title"
    ) -> dict[str, Any]:
        """Plan something.

        Args:
            title: What to call it.
            tags: Labels to attach.
        """
        return {}


def _schema(feature: BaseFeature, name: str) -> dict[str, Any]:
    return {t.name: t for t in feature.get_tools()}[name].input_schema


def test_signature_schema_carries_rich_types() -> None:
    schema = _schema(_ShapesFeature(), "shapes.plan")
    props = schema["properties"]
    assert schema["type"] == "object"
    assert props["tags"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": "Labels to attach.",
    }
    assert props["mode"] == {"type": "string", "enum": ["a", "b"]}
    assert props["speed"] == {"$ref": "#/$defs/_Speed"}
    assert schema["$defs"]["_Speed"] == {"type": "string", "enum": ["slow", "fast"]}
    assert props["at"] == {"type": "string", "format": "date-time"}
    assert props["window"] == {
        "anyOf": [{"$ref": "#/$defs/_Window"}, {"type": "null"}],
        "default": None,
    }
    assert schema["$defs"]["_Window"]["required"] == ["start"]
    assert props["limit"] == {"type": "integer", "default": 5}
    assert props["meta"]["anyOf"][0] == {"type": "object", "additionalProperties": True}


def test_required_is_exactly_the_parameters_without_defaults() -> None:
    meta = {t.name: t for t in _ShapesFeature().get_tools()}["shapes.plan"]
    assert meta.input_schema["required"] == ["title", "tags", "mode", "speed", "at"]
    assert [n for n, p in meta.parameters.items() if p.required] == [
        "title",
        "tags",
        "mode",
        "speed",
        "at",
    ]


def test_title_stripping_keeps_names_and_data() -> None:
    schema = _schema(_ShapesFeature(), "shapes.plan")
    assert "title" not in schema
    assert "title" not in schema["$defs"]["_Speed"]
    assert "title" not in schema["$defs"]["_Window"]
    # A parameter named `title`, a model field named `title`, a parameter named
    # `default`, and data containing a "title" key all survive.
    assert schema["properties"]["title"] == {"type": "string", "description": "What to call it."}
    assert schema["$defs"]["_Window"]["properties"]["title"] == {"type": "string", "default": ""}
    assert schema["properties"]["default"] == {"type": "string", "default": "x"}
    assert schema["properties"]["style"]["default"] == {"title": "bold"}


_UNSET = object()


class _SentinelFeature(BaseFeature):
    feature_name = "sentinel"

    @tool
    def find(self, query: str, cursor: Any = _UNSET) -> dict[str, Any]:
        """Find things."""
        return {}


def test_unserializable_default_stays_optional_without_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        schema = _schema(_SentinelFeature(), "sentinel.find")
    assert schema["required"] == ["query"]
    assert "default" not in schema["properties"]["cursor"]


class _OddNamesFeature(BaseFeature):
    feature_name = "odd"

    @tool
    def odd(self, model_config: int, _private: str = "", schema: str = "") -> dict[str, Any]:
        """Parameter names that collide with BaseModel internals."""
        return {}


def test_parameter_names_never_collide_with_pydantic() -> None:
    schema = _schema(_OddNamesFeature(), "odd.odd")
    assert set(schema["properties"]) == {"model_config", "_private", "schema"}
    assert schema["required"] == ["model_config"]


class _NoParamsFeature(BaseFeature):
    feature_name = "none"

    @tool
    def ping(self) -> dict[str, Any]:
        """Ping."""
        return {}


def test_no_parameter_tool_schema() -> None:
    assert _schema(_NoParamsFeature(), "none.ping") == {
        "type": "object",
        "properties": {},
        "required": [],
    }


class _VarArgsFeature(BaseFeature):
    feature_name = "varargs"

    @tool
    def bad(self, *names: str) -> dict[str, Any]:
        """Varargs."""
        return {}


class _KwArgsFeature(BaseFeature):
    feature_name = "kwargs"

    @tool
    def bad(self, **options: str) -> dict[str, Any]:
        """Kwargs."""
        return {}


class _PositionalOnlyFeature(BaseFeature):
    feature_name = "posonly"

    @tool
    def bad(self, x: int, /) -> dict[str, Any]:
        """Positional-only."""
        return {}


class _UnresolvableFeature(BaseFeature):
    feature_name = "unresolvable"

    @tool
    def bad(self, thing: NotDefinedAnywhere) -> dict[str, Any]:  # type: ignore[name-defined]  # noqa: F821
        """A hint that cannot be resolved."""
        return {}


class _Opaque:
    """A type Pydantic cannot describe."""


class _OpaqueFeature(BaseFeature):
    feature_name = "opaque"

    @tool
    def bad(self, thing: _Opaque) -> dict[str, Any]:
        """A type Pydantic cannot describe."""
        return {}


@pytest.mark.parametrize(
    ("feature", "name"),
    [
        (_VarArgsFeature, "varargs.bad"),
        (_KwArgsFeature, "kwargs.bad"),
        (_PositionalOnlyFeature, "posonly.bad"),
        (_UnresolvableFeature, "unresolvable.bad"),
        (_OpaqueFeature, "opaque.bad"),
    ],
)
def test_undescribable_signatures_fail_at_discovery(
    feature: type[BaseFeature], name: str
) -> None:
    with pytest.raises(TypeError, match=name.replace(".", r"\.")):
        feature().get_tools()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest sdk/alfred_sdk/tests/test_feature.py -q`
Expected: the new tests fail. The schema tests fail on their assertions (the schema is still assembled from type names, so `tags` has no `items` and nothing is required), and the parametrized discovery tests fail with `DID NOT RAISE`.

- [ ] **Step 3: Implement**

In `sdk/alfred_sdk/feature.py`, add `import warnings` to the stdlib imports and change the pydantic import to:

```python
from pydantic import (
    BaseModel,
    Field,
    PydanticInvalidForJsonSchema,
    PydanticSchemaGenerationError,
    create_model,
)
from pydantic.json_schema import PydanticJsonSchemaWarning
```

Add above `def _extract_tool_meta`:

```python
# ── input_schema generation (@tool methods) ──

# Keywords whose values are data, not schemas: a "title" inside them is left alone.
_LITERAL_KEYWORDS = frozenset({"default", "enum", "const", "examples"})
# Keywords whose values map names to schemas: the names are never keywords.
_SCHEMA_MAPS = frozenset({"properties", "$defs", "patternProperties"})

# Parameter kinds a dispatcher cannot pass by keyword (`AlfredClient.dispatch` calls fn(**params)).
_NOT_BY_KEYWORD_KINDS = (
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
    inspect.Parameter.POSITIONAL_ONLY,
)


def _strip_titles(node: Any) -> Any:
    """Drop Pydantic's generated ``title`` keywords: tokens for the model, no meaning.

    A property or model *named* ``title`` is kept (names under ``properties``/``$defs``
    are walked as names), and data under ``default``/``enum``/``const``/``examples``
    is never touched.
    """
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" and isinstance(value, str):
            continue
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            out[key] = {name: _strip_titles(schema) for name, schema in value.items()}
        elif key in _LITERAL_KEYWORDS:
            out[key] = value
        else:
            out[key] = _strip_titles(value)
    return out


def _signature_input_schema(
    qualified_name: str,
    params: list[inspect.Parameter],
    hints: dict[str, Any],
    doc_args: dict[str, str],
) -> dict[str, Any]:
    """Generate one JSON Schema for a tool's arguments from its signature.

    Fields are positional names with the parameter name as alias, so a parameter may
    be called anything (``model_config``, ``_private``) without colliding with BaseModel.

    Raises:
        TypeError: A parameter's type cannot be described as JSON Schema.
    """
    fields: dict[str, Any] = {}
    for index, param in enumerate(params):
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[f"p{index}"] = (
            hints.get(param.name, Any),
            Field(default, alias=param.name, description=doc_args.get(param.name) or None),
        )
    try:
        model = create_model("ToolArgs", **fields)
        with warnings.catch_warnings():
            # A default JSON cannot carry (a sentinel object) is left out of the schema;
            # the parameter stays optional, which is all a model needs to know.
            warnings.simplefilter("ignore", PydanticJsonSchemaWarning)
            schema: dict[str, Any] = model.model_json_schema(by_alias=True)
    except (PydanticSchemaGenerationError, PydanticInvalidForJsonSchema) as exc:
        raise TypeError(
            f"Tool '{qualified_name}': cannot describe its parameters as JSON Schema: {exc}"
        ) from exc
    schema = _strip_titles(schema)
    schema.setdefault("properties", {})
    schema.setdefault("required", [])
    return schema
```

Replace the body of `_extract_tool_meta` from `# Extract type hints (skip self, cls, return)` to the end of the function with:

```python
    # Extract type hints (skip self, cls, return)
    try:
        hints = get_type_hints(fn)
    except Exception as exc:  # NameError for an unresolvable forward reference, among others
        raise TypeError(f"Tool '{qualified_name}': cannot resolve its type hints: {exc}") from exc

    sig = inspect.signature(fn)
    params = [p for name, p in sig.parameters.items() if name not in ("self", "cls")]
    for param in params:
        if param.kind in _NOT_BY_KEYWORD_KINDS:
            raise TypeError(
                f"Tool '{qualified_name}': parameter '{param.name}' must be passable by "
                "keyword (no *args, **kwargs or positional-only parameters)"
            )

    parameters: dict[str, ToolParameter] = {}
    for param in params:
        has_default = param.default is not inspect.Parameter.empty
        parameters[param.name] = ToolParameter(
            type=getattr(hints.get(param.name), "__name__", str(hints.get(param.name, "Any"))),
            description=doc_args.get(param.name, ""),
            default=param.default if has_default else None,
            required=not has_default,
        )

    return ToolMeta(
        name=qualified_name,
        description=description,
        parameters=parameters,
        audience=audience,
        risk=risk,
        input_schema=_signature_input_schema(qualified_name, params, hints, doc_args),
    )
```

- [ ] **Step 4: Run the SDK and trigger tests**

Run: `uv run pytest sdk/ core/triggers/tests/ -q`
Expected: all pass. If an existing test fails, the hints of some `@tool` in the repo do not resolve; fix the hint rather than the check.

- [ ] **Step 5: Update the docs**

In `docs/sdk.md`, "What @tool extracts automatically", replace the last two table rows with:

```markdown
| Type hints on parameters | One JSON Schema for the tool's arguments (`input_schema`), generated by Pydantic: `list[X]` gets `items`, `Literal` and `Enum` get `enum`, `datetime` gets `format: date-time`, Pydantic models nest under `$defs` |
| Default values on parameters | `required`: a parameter without a default is required |
```

and add after the sentence "No schema files, no YAML, no manual registration. The Python code is the source of truth.":

```markdown
Every parameter must be passable by keyword and describable as JSON Schema. `*args`,
`**kwargs`, positional-only parameters, type hints that cannot be resolved, and types
Pydantic cannot describe raise `TypeError` at discovery, naming the tool.

A feature that builds its `ToolMeta` by hand (home-service generates its tools from
Home Assistant's service catalog) passes `ToolParameter(type=..., description=...,
required=True)` per parameter, or `json_schema={...}` for an exact schema, and
`ToolMeta` assembles `input_schema` from them. Passing `input_schema` skips the assembly.
```

Replace the sentence under "Docstring format" that begins "This produces two parameters" with:

```markdown
This produces two required parameters: `room` (`{"type": "string", "description": "The room to dim."}`) and `level` (`{"type": "integer", "description": "Brightness level 0-100."}`).
```

Under "Manifest Format", replace the JSON block with:

```json
{
  "service_name": "home-service",
  "service_endpoint": "http://home-service:8000/mcp",
  "features": [
    {
      "name": "lighting",
      "description": "Smart home lighting controls.",
      "tools": [
        {
          "name": "lighting.dim_lights",
          "description": "Dim the lights in a room.",
          "parameters": {
            "room": {"type": "str", "description": "The room to dim.", "default": null, "required": true, "json_schema": null},
            "level": {"type": "int", "description": "Brightness level 0-100.", "default": null, "required": true, "json_schema": null}
          },
          "audience": "conscious",
          "risk": "benign",
          "input_schema": {
            "type": "object",
            "properties": {
              "room": {"type": "string", "description": "The room to dim."},
              "level": {"type": "integer", "description": "Brightness level 0-100."}
            },
            "required": ["room", "level"]
          }
        }
      ]
    }
  ]
}
```

and add after it:

```markdown
`input_schema` is what System 2 offers the model. `parameters` is the older
per-parameter form. It stays on the wire while Reflex reads parameter names (its shadow
week, [#285](https://github.com/anirudhlath/alfred/issues/285)), and core derives a
schema from it for a manifest written before `input_schema` existed. Such a manifest marks
nothing required ([#300](https://github.com/anirudhlath/alfred/issues/300)), so a service
gets correct `required` lists only once it is rebuilt against this SDK.
```

In the mermaid block below it, replace `I --> K["parameters: room, level"]` with:

```
    I --> K["input_schema: room, level (required)"]
```

In `sdk/CLAUDE.md`, Gotchas, replace the line `- Complex type hints stored as \`str()\` representation in manifests (e.g., \`dict[str, Any]\`)` with:

```markdown
- Each tool's `input_schema` (one JSON Schema, generated by Pydantic from the signature, `title` keywords stripped) is what models see. `ToolParameter.type` is the legacy name string (`str()` of complex hints), kept on the wire for readers of `parameters`. `*args`/`**kwargs`/positional-only parameters, unresolvable hints and types Pydantic cannot describe raise `TypeError` at discovery
```

- [ ] **Step 6: Commit**

```bash
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/tests/test_feature.py docs/sdk.md sdk/CLAUDE.md
git commit -m "feat(sdk): generate each tool's input schema from its signature (#300, #301)"
```

---

### Task 3: Trigger feature keeps every field when it enriches `create_trigger` (#314)

**Files:**
- Modify: `core/triggers/feature.py:54-84` (`TriggerFeature.get_tools`)
- Test: `core/triggers/tests/test_feature.py`

**Interfaces:**
- Consumes: `ToolMeta.input_schema` (Tasks 1–2).
- Produces: `TriggerFeature.get_tools()` returns `create_trigger` with its `audience`, `risk` and `input_schema` intact and the enriched description.

- [ ] **Step 1: Write the failing test**

In `core/triggers/tests/test_feature.py`, add to the imports:

```python
from core.triggers.feature import TriggerFeature
from sdk.alfred_sdk.feature import tool
```

(If `TriggerFeature` is already imported inside individual tests, keep those imports; the module-level one is for the subclass below.)

Append:

```python
class _TaggedTriggerFeature(TriggerFeature):
    """create_trigger tagged with a non-default audience and risk."""

    @tool(audience="reflex", risk="critical")
    async def create_trigger(
        self,
        name: str,
        trigger_type: str,
        conditions: dict[str, Any],
        action: dict[str, Any] | None = None,
        one_shot: bool = False,
        urgency: str = "informational",
    ) -> dict[str, Any]:
        """Create a new trigger."""
        return await super().create_trigger(
            name, trigger_type, conditions, action, one_shot, urgency
        )


def test_create_trigger_enrichment_keeps_audience_risk_and_schema() -> None:
    tools = {t.name: t for t in _TaggedTriggerFeature().get_tools()}
    meta = tools["triggers.create_trigger"]
    assert (meta.audience, meta.risk) == ("reflex", "critical")
    assert "informational" in meta.description  # the enrichment still ran
    assert meta.input_schema["required"] == ["name", "trigger_type", "conditions"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest core/triggers/tests/test_feature.py::test_create_trigger_enrichment_keeps_audience_risk_and_schema -q`
Expected: FAIL, `assert ('conscious', 'benign') == ('reflex', 'critical')`.

- [ ] **Step 3: Implement**

In `core/triggers/feature.py`, add `import dataclasses` to the stdlib imports, and replace the `create_trigger` branch of the loop in `get_tools` with:

```python
            if "create_trigger" in t.name:
                enriched.append(
                    dataclasses.replace(
                        t,
                        description=(
                            t.description + "\n\n" + conditions_docs + action_docs + urgency_docs
                        ),
                    )
                )
```

`ToolMeta` is no longer used in the loop; keep its import only if the `get_tools` annotation still needs it (it does: `-> list[ToolMeta]`).

- [ ] **Step 4: Run the trigger tests**

Run: `uv run pytest core/triggers/tests/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/triggers/feature.py core/triggers/tests/test_feature.py
git commit -m "fix(triggers): keep audience, risk and schema when enriching create_trigger (#314)"
```

---

### Task 4: Core registry reads `input_schema`, tolerating old and malformed manifests

**Files:**
- Modify: `core/reflex/tool_registry.py`
- Test: `core/reflex/tests/test_tool_registry.py`

**Interfaces:**
- Consumes: `sdk.alfred_sdk.feature.ToolParameter`, `input_schema_from_parameters` (Task 1).
- Produces:
  - `ToolInfo.input_schema: dict[str, Any]`: the manifest's schema, or, when the manifest has none, one derived from `parameters` by `legacy_input_schema`. Every existing `ToolInfo(...)` call keeps working.
  - `legacy_input_schema(parameters: dict[str, dict[str, Any]]) -> dict[str, Any]`: a parameter is required when it says `"required": true`, or, lacking that key, when it has no `"default"` key (today's rule in `_tools_to_openai_format`).
  - `ToolRegistry.get_tools()` skips a malformed tool with a WARNING instead of raising.

- [ ] **Step 1: Write the failing tests**

Append to `core/reflex/tests/test_tool_registry.py`:

```python
def _registry_with(*features: dict[str, Any]) -> ToolRegistry:
    mock_redis = AsyncMock()
    mock_redis.hgetall.return_value = {
        b"svc": _make_manifest("svc", list(features)).encode(),
    }
    return ToolRegistry(mock_redis)


@pytest.mark.asyncio
async def test_manifest_input_schema_is_used_verbatim() -> None:
    schema = {
        "type": "object",
        "properties": {"days": {"type": "array", "items": {"type": "string"}}},
        "required": ["days"],
    }
    feature = {
        "name": "f",
        "tools": [
            {
                "name": "f.go",
                "parameters": {"days": {"type": "list", "default": None, "required": True}},
                "input_schema": schema,
            }
        ],
    }
    tools = await _registry_with(feature).get_tools()
    assert tools[0].input_schema == schema


@pytest.mark.asyncio
async def test_old_sdk_manifest_keeps_todays_schema() -> None:
    # An older SDK wrote "default": null for every parameter and no input_schema.
    old = {
        "name": "lighting",
        "tools": [
            {
                "name": "lighting.dim_lights",
                "description": "Dim the lights in a room.",
                "parameters": {
                    "room": {"type": "str", "description": "The room to dim.", "default": None},
                    "level": {"type": "int", "description": "0-100.", "default": None},
                },
            }
        ],
    }
    tools = await _registry_with(old).get_tools()
    assert tools[0].input_schema == {
        "type": "object",
        "properties": {
            "room": {"type": "string", "description": "The room to dim."},
            "level": {"type": "integer", "description": "0-100."},
        },
        "required": [],
    }


@pytest.mark.asyncio
async def test_parameters_without_a_default_key_stay_required() -> None:
    tools = await _registry_with(LIGHTING_FEATURE).get_tools()
    assert tools[0].input_schema["required"] == ["room", "level"]


@pytest.mark.asyncio
async def test_malformed_tool_is_skipped_and_the_rest_still_load(
    caplog: pytest.LogCaptureFixture,
) -> None:
    feature = {
        "name": "f",
        "tools": [
            {"description": "no name"},
            {"name": "f.bad_params", "parameters": {"p": "not-a-dict"}},
            {"name": "f.bad_type", "parameters": {"p": {"type": 5}}},
            {"name": "f.good", "parameters": {}},
        ],
    }
    with caplog.at_level(logging.WARNING):
        tools = await _registry_with(feature, LIGHTING_FEATURE).get_tools()
    assert [t.name for t in tools] == [
        "f.good",
        "lighting.dim_lights",
        "lighting.turn_off_lights",
    ]
    assert caplog.text.count("Skipping malformed tool") == 3
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest core/reflex/tests/test_tool_registry.py -q`
Expected: FAIL. `AttributeError: 'ToolInfo' object has no attribute 'input_schema'` for the schema tests, and `KeyError: 'name'` for the malformed-tool test.

- [ ] **Step 3: Implement**

In `core/reflex/tool_registry.py`, change `from dataclasses import dataclass` to `from dataclasses import dataclass, field`, and add after the `shared.streams` import:

```python
from sdk.alfred_sdk.feature import ToolParameter, input_schema_from_parameters
```

Add above `class ToolInfo`:

```python
def legacy_input_schema(parameters: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build a tool's input schema from a manifest written before ``input_schema``.

    Such a manifest gives every parameter ``"default": null`` (#300), so this
    reproduces exactly what the model was offered before: a parameter is required
    when it says so, or, lacking a ``required`` key, when it has no ``default`` key.
    """
    return input_schema_from_parameters(
        {
            name: ToolParameter.model_validate(
                {"type": "str", **spec, "required": spec.get("required", "default" not in spec)}
            )
            for name, spec in parameters.items()
        }
    )
```

Add to `ToolInfo`, after `risk`:

```python
    # The tool's arguments as one JSON Schema object. Empty → derived from `parameters`.
    input_schema: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.input_schema:
            object.__setattr__(self, "input_schema", legacy_input_schema(self.parameters))
```

Replace the inner `for t in feature.get("tools", []):` loop with:

```python
                for t in feature.get("tools", []):
                    try:
                        schema = t.get("input_schema")
                        tools.append(
                            ToolInfo(
                                name=t["name"],
                                description=t.get("description", ""),
                                parameters=t.get("parameters", {}),
                                feature_name=feature_name,
                                feature_description=feature_desc,
                                target_service=service_name,
                                audience=t.get("audience", "conscious"),
                                risk=t.get("risk", "benign"),
                                input_schema=schema if isinstance(schema, dict) else {},
                            )
                        )
                    except (AttributeError, KeyError, TypeError, ValueError) as exc:
                        # One bad tool must not take down every other tool (pydantic's
                        # ValidationError is a ValueError).
                        logger.warning(
                            "Skipping malformed tool %r from service '%s': %s",
                            t.get("name") if isinstance(t, dict) else t,
                            service_name,
                            exc,
                        )
```

- [ ] **Step 4: Run the registry and Reflex tests**

Run: `uv run pytest core/reflex/ tests/core/reflex/ -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add core/reflex/tool_registry.py core/reflex/tests/test_tool_registry.py
git commit -m "feat(registry): read each tool's input schema, deriving one for old manifests"
```

---

### Task 5: System 2 offers each tool's `input_schema` (#300, #301 end to end)

**Files:**
- Modify: `core/conscious/engine.py` (imports; `_TYPE_MAP`, `_to_json_schema_type` removed; `_tools_to_openai_format`; `declares_own_reason` in `_dispatch_tool_call`)
- Modify: `shared/type_map.py` (drop the `PYTHON_TO_JSON_SCHEMA` alias)
- Test: `tests/core/conscious/test_engine.py`

**Interfaces:**
- Consumes: `ToolInfo.input_schema` (Task 4); the SDK manifest (Tasks 1–2); `AlfredClient.discover_features_from_classes`, `AlfredClient.get_registration_manifest` (existing).
- Produces: `ConsciousEngine._tools_to_openai_format(tools)` returns each tool's `input_schema` as `function.parameters`, deep-copied, plus the optional `reason` property on critical tools that do not declare their own.

- [ ] **Step 1: Write the failing tests**

In `tests/core/conscious/test_engine.py`, add to the imports:

```python
import enum
import json
from typing import Any, Literal

from pydantic import BaseModel

from core.reflex.tool_registry import ToolRegistry
from core.triggers.feature import TriggerFeature
from sdk.alfred_sdk.client import AlfredClient
from sdk.alfred_sdk.feature import BaseFeature, tool
```

(`ToolInfo` is already imported from `core.reflex.tool_registry`; merge the two imports into one line, and skip any name the module already imports.)

Add after `test_tool_name_sanitization`:

```python
class _Mode(enum.Enum):
    HEAT = "heat"
    COOL = "cool"


class _Window(BaseModel):
    start: _dt.datetime
    end: _dt.datetime


class _ClimateFeature(BaseFeature):
    """Climate controls."""

    feature_name = "climate"

    @tool(risk="critical")
    async def set_schedule(
        self,
        zone: str,
        days: list[Literal["mon", "tue"]],
        at: _dt.datetime,
        mode: _Mode,
        window: _Window | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Schedule heating.

        Args:
            zone: Which zone.
            days: Days to run.
            at: When to start.
        """
        return {}


async def _offered(
    features: list[type[BaseFeature]], engine: ConsciousEngine
) -> dict[str, dict[str, Any]]:
    """The real path: SDK manifest → registry JSON → ToolRegistry → what System 2 sends."""
    client = AlfredClient(service_name="svc", service_endpoint="http://svc/mcp")
    client.discover_features_from_classes(features)
    redis = AsyncMock()
    redis.hgetall.return_value = {b"svc": json.dumps(client.get_registration_manifest()).encode()}
    tools = await ToolRegistry(redis).get_tools()
    return {
        d["function"]["name"]: d["function"]["parameters"]
        for d in engine._tools_to_openai_format(tools)
    }


@pytest.mark.asyncio
async def test_required_lists_exactly_the_parameters_without_defaults(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    """#300: a real BaseFeature manifest, through the registry, to the model."""
    offered = await _offered([TriggerFeature, _ClimateFeature], ConsciousEngine(**mock_deps))
    assert offered["triggers_delete_trigger"]["required"] == ["trigger_id"]
    assert offered["triggers_toggle_trigger"]["required"] == ["trigger_id", "enabled"]
    assert offered["triggers_create_trigger"]["required"] == ["name", "trigger_type", "conditions"]
    assert offered["triggers_list_triggers"]["required"] == []
    assert offered["climate_set_schedule"]["required"] == ["zone", "days", "at", "mode"]


@pytest.mark.asyncio
async def test_rich_types_reach_the_model(mock_deps: dict[str, AsyncMock | MagicMock]) -> None:
    """#301: list items, enums, dates and nested models survive to the tool definition."""
    schema = (await _offered([_ClimateFeature], ConsciousEngine(**mock_deps)))[
        "climate_set_schedule"
    ]
    props = schema["properties"]
    assert props["days"] == {
        "type": "array",
        "items": {"type": "string", "enum": ["mon", "tue"]},
        "description": "Days to run.",
    }
    assert props["at"] == {"type": "string", "format": "date-time", "description": "When to start."}
    assert props["mode"] == {"$ref": "#/$defs/_Mode"}
    assert schema["$defs"]["_Mode"] == {"type": "string", "enum": ["heat", "cool"]}
    assert props["window"]["anyOf"][0] == {"$ref": "#/$defs/_Window"}
    assert schema["$defs"]["_Window"]["properties"]["start"] == {
        "type": "string",
        "format": "date-time",
    }
    # Critical tools are still offered the optional `reason`.
    assert props["reason"]["type"] == "string"
    assert "reason" not in schema["required"]


def test_reason_injection_leaves_the_registry_schema_untouched(
    mock_deps: dict[str, AsyncMock | MagicMock],
) -> None:
    engine = ConsciousEngine(**mock_deps)
    tool_info = _critical_tool()
    first = engine._tools_to_openai_format([tool_info])
    second = engine._tools_to_openai_format([tool_info])
    assert first == second
    assert "reason" not in tool_info.input_schema["properties"]
```

`_critical_tool` is defined later in the module; that is fine at call time. If `mock_deps` is defined after these tests, it is still found (pytest fixtures are module-scoped names).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/conscious/test_engine.py -q -k "required_lists or rich_types or reason_injection"`
Expected: `test_required_lists…` FAILS (`[] == ['trigger_id']`), `test_rich_types…` FAILS (no `items`), and `test_reason_injection…` passes already (today's code builds a fresh dict); keep it as the regression guard for Step 3's deep copy.

- [ ] **Step 3: Implement**

In `core/conscious/engine.py`:

- add `import copy` to the stdlib imports;
- remove `from shared.type_map import PYTHON_TO_JSON_SCHEMA`;
- remove `_TYPE_MAP: ClassVar[dict[str, str]] = PYTHON_TO_JSON_SCHEMA` and the whole `_to_json_schema_type` method;
- replace `_tools_to_openai_format` with:

```python
    def _tools_to_openai_format(self, tools: list[ToolInfo]) -> list[dict[str, Any]]:
        """Convert ToolInfo list to OpenAI function-calling format (used by LiteLLM).

        Each tool's ``input_schema`` is offered as is. It is deep-copied, because the
        ``reason`` injected for critical tools must never reach the registry's copy.
        """
        openai_tools: list[dict[str, Any]] = []
        for t in tools:
            schema = copy.deepcopy(t.input_schema)
            properties: dict[str, Any] = schema.setdefault("properties", {})
            if t.risk == "critical" and self._REASON_PARAM not in properties:
                properties[self._REASON_PARAM] = {
                    "type": "string",
                    "description": (
                        "One sentence, addressed to the user, saying why this action is "
                        "needed. It is shown on the confirmation prompt."
                    ),
                }

            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": self._sanitize_tool_name(t.name),
                        "description": t.description,
                        "parameters": schema,
                    },
                }
            )
        return openai_tools
```

- in `_dispatch_tool_call`, replace `declares_own_reason = self._REASON_PARAM in t.parameters` with:

```python
                declares_own_reason = self._REASON_PARAM in t.input_schema.get("properties", {})
```

In `shared/type_map.py`, delete the alias and its comment:

```python
# Alias kept for core/conscious/engine.py until it reads the SDK's input_schema.
PYTHON_TO_JSON_SCHEMA = JSON_TYPE_BY_PYTHON_NAME
```

Then confirm nothing else uses it: `grep -rn "PYTHON_TO_JSON_SCHEMA\|_to_json_schema_type" --include='*.py' . | grep -v .venv` prints nothing.

- [ ] **Step 4: Run the conscious tests**

Run: `uv run pytest tests/core/conscious/ -q`
Expected: all pass, including the existing `test_tools_to_openai_format`, `test_critical_tools_get_a_reason_parameter` and the own-`reason` tests (their hand-built `ToolInfo`s go through `legacy_input_schema`).

- [ ] **Step 5: Commit**

```bash
git add core/conscious/engine.py shared/type_map.py tests/core/conscious/test_engine.py
git commit -m "feat(conscious): offer each tool's input schema to the model (#300, #301)"
```

---

### Task 6: Verify, compare with master on the evals, and open the PRs on the owner's go

**Files:** none changed unless a check fails.

- [ ] **Step 1: Full gate (once)**

```bash
cd ~/code/.worktrees/alfred/pydantic-ai-adoption
uv run ruff check . && uv run ruff format --check . \
  && uv run mypy --strict alfred_cli/ alfredctl/ bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/ \
  && PYTHONDONTWRITEBYTECODE=1 uv run pytest -x -q
```

Expected: all green. Fix anything red in the task that owns it, with its own commit.

- [ ] **Step 2: Evals against master**

This PR changes the tool schemas System 2 offers, so it must match master on the evals. Setup is in `docs/evals.md`. Run one eval at a time: check `docker ps` for another session's eval stack first and wait for it to finish. Tell the owner the bound before starting: two runs of `home_control` + `conversation` at 5 epochs on the shared vLLM.

```bash
# branch
uv run alfred evals run home_control conversation --epochs 5
# master baseline in a throwaway detached worktree
git fetch origin master
git worktree add --detach ~/code/.worktrees/alfred/master-baseline origin/master
cd ~/code/.worktrees/alfred/master-baseline && uv venv --python 3.13 && uv sync --all-extras
uv run alfred evals run home_control conversation --epochs 5
cd ~/code/.worktrees/alfred/pydantic-ai-adoption
git worktree remove ~/code/.worktrees/alfred/master-baseline
```

Expected: no golden regresses beyond run-to-run noise (vLLM is not deterministic at temperature 0; compare pass rates over the 5 epochs, not single answers). Keep both scorecards for the PR body.

- [ ] **Step 3: Hygiene**

Run both greps from `~/code/alfred-deploy/PWA-EXPOSURE-RUNBOOK.md` §Secret hygiene in this worktree; both must print nothing. Then check that neither the commit messages nor the diff name a model. Set `MODEL_NAMES` in your shell to an `-E` alternation of the model family names (never write that list into a committed file):

```bash
git log --format=%B origin/master..HEAD | grep -inE "$MODEL_NAMES"
git diff origin/master...HEAD | grep -inE "$MODEL_NAMES"
```

Expected: no matches.

- [ ] **Step 4: STOP: report to the owner**

Report the gate result and both eval scorecards, and ask for the go to push. Do not push, open PRs or edit issues before it.

- [ ] **Step 5 (on the owner's go): Submit the stack, fix titles and bodies, update #301**

```bash
gh stack submit --auto --open
gh stack view          # note the two PR numbers: spec (bottom) and this one
```

Retitle and rewrite both PRs to match `.github/pull_request_template.md`:

- Spec PR: title `docs: design spec for adopting Pydantic AI across System 2, the Librarian and Reflex`; body "What & why" says it is the design for #317 and that each later PR carries its own plan; `Refs #317`.
- This PR: title `feat(sdk): one JSON Schema per tool, with required parameters`; body "What & why" lists the change per task, `Closes #300`, `Closes #301`, `Closes #314`, `Refs #317`, and pastes both eval scorecards. Tick the checklist items that hold. Both bodies end with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

Use `gh pr edit <n> --title "…" --body-file <file>`, writing the body files in the session scratchpad.

Update #301's first acceptance criterion to the owner's one-schema-per-tool decision (CLAUDE.md: a PR that changes a ticket's scope edits the issue): fetch the body with `gh issue view 301 --json body -q .body > <scratchpad>/301.md`, replace the line beginning `- [ ] The SDK emits a JSON Schema per parameter` with

```markdown
- [ ] The SDK emits one JSON Schema per tool (`input_schema`), generated by Pydantic from the signature, and System 2 passes it through instead of re-deriving types (owner's decision 2026-10-08: one schema per tool, not per parameter)
```

then `gh issue edit 301 --body-file <scratchpad>/301.md`.

Then wait for CI (`ci-ok`) and the owner's merge order. Merging deploys.

---

### Task 7: home-service marks required parameters (develop against the local SDK)

Part 1b of the stack, in the separate `anirudhlath/alfred-home-service` repo. It can be built and tested now against the local SDK; Task 8 pins it once PR 1 has merged.

**Files (home-service repo):**
- Modify: `app/capability_generator.py` (`FieldSpec`, `_field_spec`, `build_tool_meta`)
- Test: `tests/test_capability_generator.py`

**Interfaces:**
- Consumes: `alfred_sdk.feature.ToolParameter(required=...)`, `ToolMeta.input_schema` (Tasks 1–2).
- Produces: every generated tool's `input_schema["required"]` lists `target` for targeted services, every Home Assistant field the service catalog marks `required`, and `domain`/`service` for `home.call_service`. Descriptions no longer end in " (required)".

- [ ] **Step 1: Worktree and the local SDK overlay**

```bash
git -C ~/code/alfred-deploy/home-service fetch origin main
git -C ~/code/alfred-deploy/home-service worktree add -b feat/required-tool-params \
  ~/code/.worktrees/home-service/required-tool-params origin/main
H=~/code/.worktrees/home-service/required-tool-params; cd "$H"
uv venv --python 3.13 && uv sync --all-extras
uv pip install -e ~/code/.worktrees/alfred/pydantic-ai-adoption/sdk   # re-run after any uv sync
uv run python -c "import alfred_sdk.feature as f; print(f.__file__, hasattr(f, 'input_schema_from_parameters'))"
```

Expected: the path is under `pydantic-ai-adoption/sdk` and the flag is `True`.

Do not use `~/code/.worktrees/home-service/evals-main`: the eval harness reads it.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_capability_generator.py`:

```python
def _vacuum_index() -> EntityIndex:
    index = EntityIndex()
    index.rebuild(
        entity_registry=[],
        device_registry=[],
        area_registry=[],
        states={
            "vacuum.robo": HAEntityState(entity_id="vacuum.robo", state="docked", attributes={})
        },
    )
    return index


def test_required_fields_and_target_are_required(generator: CapabilityGenerator) -> None:
    catalog: dict[str, Any] = {
        "vacuum": {
            "send_command": {
                "name": "Send command",
                "description": "Send a command.",
                "fields": {
                    "command": {"description": "Command to send.", "required": True},
                    "params": {"description": "Extra parameters.", "selector": {"object": {}}},
                },
                "target": {"entity": {"domain": "vacuum"}},
            }
        }
    }
    index = _vacuum_index()
    spec = {s.tool_name: s for s in generator.generate(catalog, index)}["home.vacuum_send_command"]
    meta = generator.build_tool_meta(spec, index)
    assert meta.input_schema["required"] == ["target", "command"]
    assert meta.parameters["command"].required is True
    assert meta.parameters["params"].required is False
    assert "(required)" not in meta.parameters["command"].description


def test_call_service_requires_domain_and_service(
    generator: CapabilityGenerator,
    specs: list[GeneratedToolSpec],
    built_index: EntityIndex,
) -> None:
    meta = generator.build_tool_meta(_by_name(specs)["home.call_service"], built_index)
    assert meta.input_schema["required"] == ["domain", "service"]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_capability_generator.py -q`
Expected: FAIL, `assert [] == ['target', 'command']`.

- [ ] **Step 4: Implement**

In `app/capability_generator.py`:

Add `required: bool = False` as the last field of `FieldSpec`.

Replace `_field_spec` with:

```python
def _field_spec(name: str, fdef: dict[str, Any] | None) -> FieldSpec:
    fdef = fdef or {}
    description = str(fdef.get("description") or fdef.get("name") or name)
    example = fdef.get("example")
    if example is not None:
        description += f" Example: {example}."
    return FieldSpec(
        name=name,
        type=_field_type(fdef),
        description=description,
        required=bool(fdef.get("required")),
    )
```

In `build_tool_meta`, mark `domain` and `service` `required=True` in the escape-hatch parameters, mark `target` `required=True`, and pass each field's flag:

```python
                "domain": ToolParameter(
                    type="str", description="HA service domain, e.g. 'light'.", required=True
                ),
                "service": ToolParameter(
                    type="str", description="Service name, e.g. 'turn_on'.", required=True
                ),
```

```python
            if spec.targeted:
                parameters["target"] = ToolParameter(
                    type="str",
                    description=self._target_description(spec.domain, index),
                    required=True,
                )
            for f in spec.fields:
                parameters[f.name] = ToolParameter(
                    type=f.type, description=f.description, required=f.required
                )
```

- [ ] **Step 5: Run the home-service gate**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ alfred_ext/ && uv run pytest -q
```

Expected: all green.

- [ ] **Step 6: Commit (local only)**

```bash
git add app/capability_generator.py tests/test_capability_generator.py
git commit -m "feat: mark required tool parameters in the manifest"
```

---

### Task 8: home-service pins the merged SDK and opens its PR (after PR 1 merges)

**Precondition:** PR 1 (`feat/sdk-input-schema`) has merged to alfred's `master`.

- [ ] **Step 1: Pin the merge commit**

```bash
H=~/code/.worktrees/home-service/required-tool-params; cd "$H"
SHA=$(gh api repos/anirudhlath/alfred/commits/master -q .sha)   # confirm it is PR 1's squash commit
git -C ~/code/alfred-deploy/home-service fetch origin main && git rebase origin/main
```

In `pyproject.toml`, set the `alfred-sdk` source `rev` to `$SHA` and update the comment above it to say the pin ships `input_schema` and `ToolParameter.required` (alfred#300/#301). Then:

```bash
uv lock --upgrade-package alfred-sdk && uv sync --all-extras
uv run python -c "import alfred_sdk.feature as f; print(f.__file__)"   # under .venv, not the overlay
uv run ruff check . && uv run ruff format --check . && uv run mypy app/ alfred_ext/ && uv run pytest -q
git add pyproject.toml uv.lock && git commit -m "build: pin alfred-sdk to the commit that ships input_schema"
```

- [ ] **Step 2: Hygiene, then STOP for the owner's go**

Run the runbook's two greps in this worktree (both print nothing). Report and ask for the go to push.

- [ ] **Step 3 (on the go): Push and open the PR**

```bash
git push -u origin feat/required-tool-params
gh pr create --title "feat: mark required tool parameters in the manifest" --body-file <scratchpad>/hs-pr.md
```

The body says what changed (required `target`, catalog-required fields, `domain`/`service` on `home.call_service`; " (required)" dropped from descriptions; SDK pin bumped), `Refs anirudhlath/alfred#300`, and ends with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

After it merges, the next alfred deploy installs it. Rerun `alfred evals run home_control conversation --epochs 5` once against master and post the scorecard on alfred#317.
