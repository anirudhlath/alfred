# HA Plan 1 — Sovereign Service Credential Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sovereign services declare their credential needs (schema + push endpoint) in their SDK registration manifest; Alfred core stores credentials in the OS keyring, exposes them through the existing schema-driven integrations API/UI, and event-drivenly re-pushes them whenever a service (re)registers.

**Architecture:** The SDK's `ServiceManifest` gains `credentials_schema`/`credentials_endpoint` and `AlfredClient.register()` publishes a new `ServiceRegistered` bus event to `alfred:events` after the registry hset. Core's web channel merges registry-declared services into `GET /api/integrations` (marked `"kind": "service"`), handles PUT/DELETE/status for them (keyring + httpx push / health proxy), and runs a `channels-credentials` consumer on `alfred:events` that re-pushes stored credentials on every `ServiceRegistered` — self-healing, no polling. The React SPA needs only a `kind` badge; the schema-driven `IntegrationCard` already renders everything else.

**Tech Stack:** Python 3.13, Pydantic v2, FastAPI, httpx (`httpx.MockTransport` in tests), redis.asyncio streams/consumer groups, keyring (InMemoryKeyring in tests), loguru (core) / stdlib logging (SDK — dependency-minimal), pytest + pytest-asyncio, React 19 + vitest + testing-library.

**This is Plan 1 of 3.** Spec: `docs/superpowers/specs/2026-07-15-real-home-ha-integration-design.md` (Section 1). Fixed interface contracts C1–C5 (from the shared contracts doc) are reproduced inline where used — do not rename anything. Plan 2 (home-service) consumes the SDK changes; Plan 3 (core proactivity/safety) consumes the `audience`/`risk` manifest fields.

## Global Constraints

- Python 3.13+, Pydantic v2, async-first, type hints on all signatures.
- `mypy --strict` must pass on `bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/` (this includes `sdk/tests/` and `sdk/alfred_sdk/tests/` — SDK test code must be fully typed; root `tests/` is not in the mypy target list).
- `ruff` line-length 100; run `ruff check . --fix && ruff format .` before every commit.
- Test command: `.venv/bin/python -m pytest -x -q` (use the repo `.venv`; in a fresh worktree run `uv venv --python 3.13` then `uv pip install -e ".[dev,memory,voice,integrations]"` first).
- Redis stream wire format everywhere: `await redis.xadd(STREAM, {"event": event.model_dump_json()})` — precedent `core/triggers/engine.py:61`.
- Stream/key constants come ONLY from `shared/streams.py` in monorepo code. The SDK is standalone (never imports `bus/`, `core/`, or `shared/`) and duplicates string constants with a comment — precedent `sdk/alfred_sdk/client.py:138` (`CONTEXT_KEY_PREFIX`).
- SDK event models are wire-compatible mirrors of `bus/schemas/events.py`, enforced by `sdk/tests/test_schema_compatibility.py`.
- New core code uses loguru (`from loguru import logger`); SDK code keeps stdlib `logging` (SDK deps are pydantic + redis only).
- `redis.asyncio` calls need `# type: ignore[misc]` on awaits (precedent `core/reflex/runner.py:57`, `core/notifications/delivery.py:50`).
- Import `ensure_consumer_group` from `core.reflex.runner` — never reimplement.
- Commits: end every commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Work on a feature branch in a worktree **inside the `alfred/` repo** (e.g. `alfred/.worktrees/ha-plan1`), never at the workspace root.

## File Structure

| File | Responsibility |
|---|---|
| `sdk/alfred_sdk/feature.py` | + `CredentialField`/`CredentialSchema`, `ToolAudience`/`ToolRisk` aliases, `ToolManifest.audience/risk`, `ServiceManifest.credentials_*`, `ToolMeta`/`@tool` propagation |
| `sdk/alfred_sdk/events.py` | + `ServiceRegistered` mirror |
| `sdk/alfred_sdk/client.py` | + constructor params, `EVENTS_STREAM` const, manifest fields, publish on `register()` |
| `sdk/alfred_sdk/__init__.py` | + export `CredentialField`, `CredentialSchema` |
| `bus/schemas/events.py` | + canonical `ServiceRegistered` |
| `core/channels/service_credentials.py` (new) | registry-manifest reads, body validation, keyring helpers, credential push, health convention, `credential_push_worker` |
| `core/channels/web_server.py` | merged GET, service branches for PUT/DELETE/status, lifespan wiring of the worker |
| `web/src/lib/types.ts`, `web/src/pages/IntegrationCard.tsx` | `kind` field + "external service" badge |
| Tests | `sdk/alfred_sdk/tests/test_feature.py`, `sdk/tests/test_schema_compatibility.py`, `sdk/tests/test_client_register.py` (new), `tests/core/channels/test_service_credentials.py` (new), `tests/core/channels/test_service_integrations_api.py` (new), `tests/core/channels/conftest.py`, `web/src/pages/IntegrationCard.test.tsx`, `web/src/pages/SettingsPage.test.tsx` |

---

### Task 1: SDK credential models + `ServiceManifest` credential fields

**Context (read first):** `sdk/alfred_sdk/feature.py:15-47` (manifest models), `core/integrations/base.py:35-50` (the core `CredentialField`/`CredentialSchema` the SDK copies must stay JSON-identical to), `sdk/tests/test_schema_compatibility.py` (compat-test style; `sdk/tests/` may import `bus`/`core` — these are monorepo-side tests, not shipped with the package).

**Files:**
- Modify: `sdk/alfred_sdk/feature.py:15-47`
- Modify: `sdk/alfred_sdk/__init__.py`
- Test: `sdk/alfred_sdk/tests/test_feature.py`
- Test: `sdk/tests/test_schema_compatibility.py`

**Interfaces:**
- Produces (contract C1, exact): in `sdk/alfred_sdk/feature.py` —
  ```python
  class CredentialField(BaseModel):
      label: str
      field_type: Literal["text", "password", "url"] = "text"
      required: bool = True
      placeholder: str = ""
      default: str = ""
      help_text: str = ""
      transient: bool = False

  class CredentialSchema(BaseModel):
      fields: dict[str, CredentialField]

  class ServiceManifest(BaseModel):
      service_name: str
      service_endpoint: str
      features: list[FeatureManifest] = []
      credentials_schema: CredentialSchema | None = None
      credentials_endpoint: str | None = None
  ```
- Consumed by: Task 4 (`AlfredClient`), Task 5/6 (core parses the same JSON via `core.integrations.base.CredentialSchema`), Plan 2 (home-service declares its schema).

- [ ] **Step 1: Write the failing tests**

Append to `sdk/alfred_sdk/tests/test_feature.py`:

```python
# ── Credential models (contract C1) ──


def test_credential_field_defaults() -> None:
    from sdk.alfred_sdk.feature import CredentialField

    field = CredentialField(label="HA URL")
    assert field.field_type == "text"
    assert field.required is True
    assert field.placeholder == ""
    assert field.default == ""
    assert field.help_text == ""
    assert field.transient is False


def test_credential_schema_dumps_fields() -> None:
    from sdk.alfred_sdk.feature import CredentialField, CredentialSchema

    schema = CredentialSchema(
        fields={"url": CredentialField(label="HA URL", field_type="url")}
    )
    dumped = schema.model_dump()
    assert dumped["fields"]["url"]["label"] == "HA URL"
    assert dumped["fields"]["url"]["field_type"] == "url"


def test_service_manifest_credential_fields_default_none() -> None:
    from sdk.alfred_sdk.feature import ServiceManifest

    manifest = ServiceManifest(service_name="svc", service_endpoint="http://x/mcp")
    dumped = manifest.model_dump()
    assert dumped["credentials_schema"] is None
    assert dumped["credentials_endpoint"] is None


def test_service_manifest_carries_credentials() -> None:
    from sdk.alfred_sdk.feature import CredentialField, CredentialSchema, ServiceManifest

    manifest = ServiceManifest(
        service_name="home-service",
        service_endpoint="http://localhost:8000/mcp",
        credentials_schema=CredentialSchema(
            fields={"token": CredentialField(label="Token", field_type="password")}
        ),
        credentials_endpoint="http://localhost:8000/credentials",
    )
    dumped = manifest.model_dump()
    assert dumped["credentials_endpoint"] == "http://localhost:8000/credentials"
    assert dumped["credentials_schema"]["fields"]["token"]["field_type"] == "password"
```

Append to `sdk/tests/test_schema_compatibility.py` (uses the existing `_get_field_names` helper at line 68):

```python
def test_credential_models_match_core_field_shape() -> None:
    """SDK CredentialField/CredentialSchema must stay JSON-identical to core's.

    The JSON contract is the coupling (Pillar 3) — the SDK never imports core,
    so this test is the only guard against drift with core/integrations/base.py.
    """
    from core.integrations.base import CredentialField as CoreField
    from core.integrations.base import CredentialSchema as CoreSchema
    from sdk.alfred_sdk.feature import CredentialField as SdkField
    from sdk.alfred_sdk.feature import CredentialSchema as SdkSchema

    assert _get_field_names(CoreField) == _get_field_names(SdkField)
    assert _get_field_names(CoreSchema) == _get_field_names(SdkSchema)

    # Round-trip: SDK-serialized schema parses as the core model with values intact.
    sdk_schema = SdkSchema(
        fields={"token": SdkField(label="Token", field_type="password", transient=False)}
    )
    core_schema = CoreSchema.model_validate_json(sdk_schema.model_dump_json())
    assert core_schema.fields["token"].label == "Token"
    assert core_schema.fields["token"].field_type == "password"

    # Defaults must match too — core fills defaults for fields the SDK omitted.
    assert CoreField(label="x").model_dump() == SdkField(label="x").model_dump()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest sdk/alfred_sdk/tests/test_feature.py sdk/tests/test_schema_compatibility.py -q`
Expected: FAIL — `ImportError: cannot import name 'CredentialField' from 'sdk.alfred_sdk.feature'`

- [ ] **Step 3: Implement the models**

In `sdk/alfred_sdk/feature.py`:

1. Change the typing import (line 9) to include `Literal`:

```python
from typing import Any, Literal, TypeVar, overload
```

2. Insert immediately after the `ToolParameter` class (after line 23, before `ToolManifest`):

```python
ToolAudience = Literal["reflex", "conscious"]
ToolRisk = Literal["benign", "elevated", "critical"]


class CredentialField(BaseModel):
    """Describes one credential input field for a sovereign service.

    Field shape MUST stay identical to core/integrations/base.py CredentialField.
    The JSON contract is the coupling — the SDK never imports core. Guarded by
    sdk/tests/test_schema_compatibility.py::test_credential_models_match_core_field_shape.
    """

    label: str
    field_type: Literal["text", "password", "url"] = "text"
    required: bool = True
    placeholder: str = ""
    default: str = ""  # Pre-filled value (use for sensible defaults like known URLs)
    help_text: str = ""
    transient: bool = False  # If True, value is pushed to the service but not persisted


class CredentialSchema(BaseModel):
    """Describes all credential fields for a sovereign service."""

    fields: dict[str, CredentialField]
```

(`ToolAudience`/`ToolRisk` are used by Task 2 — defining them here keeps this a single edit to the import block.)

3. Replace the `ServiceManifest` class (lines 42-47) with:

```python
class ServiceManifest(BaseModel):
    """Schema for a service's full registration manifest."""

    service_name: str
    service_endpoint: str
    features: list[FeatureManifest] = []
    credentials_schema: CredentialSchema | None = None
    credentials_endpoint: str | None = None
```

4. In `sdk/alfred_sdk/__init__.py`, replace the whole file with:

```python
"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .feature import BaseFeature, CredentialField, CredentialSchema, tool
from .telemetry import track_event, track_latency, track_tokens

__all__ = [
    "AlfredClient",
    "BaseFeature",
    "CredentialField",
    "CredentialSchema",
    "tool",
    "track_event",
    "track_latency",
    "track_tokens",
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest sdk/ -q`
Expected: PASS (all SDK tests, including the pre-existing ones)

- [ ] **Step 5: Lint, type-check, commit**

```bash
cd <worktree-root>
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict sdk/
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/__init__.py \
    sdk/alfred_sdk/tests/test_feature.py sdk/tests/test_schema_compatibility.py
git commit -m "feat(sdk): CredentialField/CredentialSchema models + ServiceManifest credential fields

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `@tool` audience/risk → `ToolMeta` → `ToolManifest`

**Context (read first):** `sdk/alfred_sdk/feature.py:26-31` (`ToolManifest`), `:53-59` (`ToolMeta`), `:111-154` (`_extract_tool_meta`), `:169-184` (`BaseFeature.get_tools`), `:191-205` (`to_manifest`), `:217-254` (`tool` decorator + overloads). Core's `ToolRegistry` (`core/reflex/tool_registry.py:66-79`) reads manifests permissively with `.get(...)` — the new manifest keys need **no core-side change in this plan** (Plan 3 consumes them).

**Files:**
- Modify: `sdk/alfred_sdk/feature.py`
- Test: `sdk/alfred_sdk/tests/test_feature.py`

**Interfaces:**
- Consumes: `ToolAudience`, `ToolRisk` aliases from Task 1.
- Produces (contract C1, exact):
  - `ToolManifest` gains `audience: ToolAudience = "conscious"` and `risk: ToolRisk = "benign"`.
  - `ToolMeta` gains the same two fields (same defaults).
  - `@tool(audience=..., risk=...)` kwargs; `_tool_overrides` dict carries `"audience"`/`"risk"`; `BaseFeature.get_tools()` and `to_manifest()` propagate them.
- Consumed by: Plan 2 (home-service `CapabilityGenerator` tags tools), Plan 3 (`core/routing/risk.py` reads `risk` from registry manifests).

- [ ] **Step 1: Write the failing tests**

Append to `sdk/alfred_sdk/tests/test_feature.py`:

```python
# ── audience / risk (contract C1) ──


def test_tool_decorator_default_audience_and_risk() -> None:
    @tool
    def my_tool(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_tool._tool_overrides["audience"] == "conscious"  # type: ignore[attr-defined]
    assert my_tool._tool_overrides["risk"] == "benign"  # type: ignore[attr-defined]


def test_tool_decorator_audience_and_risk_kwargs() -> None:
    @tool(audience="reflex", risk="critical")
    def my_tool(x: int) -> str:
        """Do something."""
        return str(x)

    assert my_tool._tool_overrides["audience"] == "reflex"  # type: ignore[attr-defined]
    assert my_tool._tool_overrides["risk"] == "critical"  # type: ignore[attr-defined]


class _TaggedFeature(BaseFeature):
    """Feature with audience/risk-tagged tools."""

    feature_name = "tagged"

    @tool(audience="reflex")
    def turn_on(self, room: str) -> dict[str, Any]:
        """Turn on lights.

        Args:
            room: The room.
        """
        return {"room": room}

    @tool(risk="critical")
    def unlock(self, door: str) -> dict[str, Any]:
        """Unlock a door.

        Args:
            door: The door.
        """
        return {"door": door}


def test_get_tools_carries_audience_and_risk() -> None:
    feature = _TaggedFeature()
    metas = {t.name: t for t in feature.get_tools()}
    assert metas["tagged.turn_on"].audience == "reflex"
    assert metas["tagged.turn_on"].risk == "benign"
    assert metas["tagged.unlock"].audience == "conscious"
    assert metas["tagged.unlock"].risk == "critical"


def test_to_manifest_carries_audience_and_risk() -> None:
    feature = _TaggedFeature()
    manifest_tools = {t.name: t for t in feature.to_manifest().tools}
    assert manifest_tools["tagged.turn_on"].audience == "reflex"
    assert manifest_tools["tagged.unlock"].risk == "critical"

    dumped = feature.to_manifest().model_dump()
    by_name = {t["name"]: t for t in dumped["tools"]}
    assert by_name["tagged.turn_on"]["audience"] == "reflex"
    assert by_name["tagged.turn_on"]["risk"] == "benign"
    assert by_name["tagged.unlock"]["audience"] == "conscious"
    assert by_name["tagged.unlock"]["risk"] == "critical"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest sdk/alfred_sdk/tests/test_feature.py -q`
Expected: FAIL — `KeyError: 'audience'` (decorator tests) and `AttributeError: 'ToolMeta' object has no attribute 'audience'`

- [ ] **Step 3: Implement propagation**

In `sdk/alfred_sdk/feature.py`, make these five edits:

1. Replace `ToolManifest` (lines 26-31) with:

```python
class ToolManifest(BaseModel):
    """Schema for a single tool in the manifest."""

    name: str
    description: str = ""
    parameters: dict[str, ToolParameter] = {}
    audience: ToolAudience = "conscious"
    risk: ToolRisk = "benign"
```

2. Replace `ToolMeta` with:

```python
@dataclass(frozen=True)
class ToolMeta:
    """Extracted metadata for a single tool method."""

    name: str
    description: str
    parameters: dict[str, ToolParameter]
    audience: ToolAudience = "conscious"
    risk: ToolRisk = "benign"
```

3. Replace the `_extract_tool_meta` signature and its final `return` (keep the body between them unchanged):

```python
def _extract_tool_meta(
    fn: Any,
    feature_name: str,
    name_override: str | None = None,
    description_override: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> ToolMeta:
```

and

```python
    return ToolMeta(
        name=qualified_name,
        description=description,
        parameters=parameters,
        audience=audience,
        risk=risk,
    )
```

4. In `BaseFeature.get_tools()`, replace the `_extract_tool_meta(...)` call with:

```python
            meta = _extract_tool_meta(
                attr,
                feature_name=self.feature_name,
                name_override=overrides.get("name"),
                description_override=overrides.get("description"),
                audience=overrides.get("audience", "conscious"),
                risk=overrides.get("risk", "benign"),
            )
```

5. In `BaseFeature.to_manifest()`, replace the `ToolManifest(...)` construction with:

```python
        tool_manifests = [
            ToolManifest(
                name=t.name,
                description=t.description,
                parameters=dict(t.parameters),
                audience=t.audience,
                risk=t.risk,
            )
            for t in self.get_tools()
        ]
```

6. Replace the second `@overload` and the `tool()` implementation (lines 220-254) with:

```python
@overload
def tool(fn: _F) -> _F: ...


@overload
def tool(
    *,
    description: str | None = None,
    name: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> Callable[[_F], _F]: ...


def tool(
    fn: _F | None = None,
    *,
    description: str | None = None,
    name: str | None = None,
    audience: ToolAudience = "conscious",
    risk: ToolRisk = "benign",
) -> _F | Callable[[_F], _F]:
    """Mark a BaseFeature method as a tool.

    Supports bare ``@tool`` and ``@tool(description=..., name=..., audience=..., risk=...)``.
    Metadata is auto-extracted from docstring + type hints at discovery time.
    ``audience`` gates which engine sees the tool ("reflex" tools also reach
    Conscious); ``risk`` gates dispatch ("critical" requires user confirmation).
    """

    def decorator(f: _F) -> _F:
        f._tool_marker = True  # type: ignore[attr-defined]
        f._tool_overrides = {  # type: ignore[attr-defined]
            "description": description,
            "name": name,
            "audience": audience,
            "risk": risk,
        }
        return f

    if fn is not None:
        return decorator(fn)
    return decorator
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest sdk/ -q`
Expected: PASS

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict sdk/
git add sdk/alfred_sdk/feature.py sdk/alfred_sdk/tests/test_feature.py
git commit -m "feat(sdk): @tool audience/risk kwargs propagated through ToolMeta to ToolManifest

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `ServiceRegistered` event — bus canonical + SDK mirror

**Context (read first):** `bus/schemas/events.py:18-24` (`BaseEvent`), `sdk/alfred_sdk/events.py` (mirror module + its header comment), `sdk/tests/test_schema_compatibility.py:73-89` (field-parity test to extend).

**Files:**
- Modify: `bus/schemas/events.py` (append after `ReflexObservation`, line 149)
- Modify: `sdk/alfred_sdk/events.py` (append after `ActionResult`, line 57)
- Test: `sdk/tests/test_schema_compatibility.py`

**Interfaces:**
- Produces (contract C2, exact — identical class in BOTH files, each subclassing that file's own `BaseEvent`):
  ```python
  class ServiceRegistered(BaseEvent):
      event_type: str = "service_registered"
      service_name: str
      credentials_endpoint: str | None = None
      has_credentials_schema: bool = False
  ```
- Consumed by: Task 4 (SDK publishes), Task 7 (core consumes from `EVENTS_STREAM`).

- [ ] **Step 1: Write the failing tests**

Append to `sdk/tests/test_schema_compatibility.py`:

```python
def test_service_registered_roundtrip_sdk_to_bus() -> None:
    """Serialize an SDK ServiceRegistered, deserialize as bus ServiceRegistered."""
    from bus.schemas.events import ServiceRegistered as BusReg
    from sdk.alfred_sdk.events import ServiceRegistered as SdkReg

    sdk_event = SdkReg(
        source="home-service",
        service_name="home-service",
        credentials_endpoint="http://localhost:8000/credentials",
        has_credentials_schema=True,
    )
    bus_event = BusReg.model_validate_json(sdk_event.model_dump_json())

    assert bus_event.event_type == "service_registered"
    assert bus_event.service_name == "home-service"
    assert bus_event.credentials_endpoint == "http://localhost:8000/credentials"
    assert bus_event.has_credentials_schema is True
    assert bus_event.event_id == sdk_event.event_id


def test_service_registered_defaults() -> None:
    """A service without credential support publishes a minimal event."""
    from sdk.alfred_sdk.events import ServiceRegistered

    event = ServiceRegistered(source="plain-service", service_name="plain-service")
    assert event.credentials_endpoint is None
    assert event.has_credentials_schema is False
```

Then in the existing `test_shared_schemas_have_same_fields` (line 73), extend the pair list to:

```python
    for bus_cls, sdk_cls in [
        (bus.BaseEvent, sdk.BaseEvent),
        (bus.StateChangedEvent, sdk.StateChangedEvent),
        (bus.ActionRequest, sdk.ActionRequest),
        (bus.ActionResult, sdk.ActionResult),
        (bus.ServiceRegistered, sdk.ServiceRegistered),
    ]:
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest sdk/tests/test_schema_compatibility.py -q`
Expected: FAIL — `ImportError: cannot import name 'ServiceRegistered'`

- [ ] **Step 3: Implement both event classes**

Append to `bus/schemas/events.py` (after `ReflexObservation`):

```python
class ServiceRegistered(BaseEvent):
    """A sovereign service (re)registered its manifest in the tool registry.

    Published by the SDK's AlfredClient.register() AFTER the registry hset,
    so consumers can immediately read the manifest. The channels process
    consumes this to (re)push stored credentials to the service's
    credentials_endpoint (self-healing, event-driven — no polling).
    """

    event_type: str = "service_registered"
    service_name: str
    credentials_endpoint: str | None = None
    has_credentials_schema: bool = False
```

Append to `sdk/alfred_sdk/events.py` (after `ActionResult`):

```python
class ServiceRegistered(BaseEvent):
    """A sovereign service (re)registered its manifest. Mirrors bus/schemas/events.py."""

    event_type: str = "service_registered"
    service_name: str
    credentials_endpoint: str | None = None
    has_credentials_schema: bool = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest sdk/tests/test_schema_compatibility.py tests/bus/ -q`
Expected: PASS

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict bus/ sdk/
git add bus/schemas/events.py sdk/alfred_sdk/events.py sdk/tests/test_schema_compatibility.py
git commit -m "feat(bus,sdk): ServiceRegistered event with SDK wire-compatible mirror

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `AlfredClient` credential params + `register()` publishes `ServiceRegistered`

**Context (read first):** `sdk/alfred_sdk/client.py:27-39` (`__init__`), `:137-138` (SDK constant-duplication precedent), `:156-166` (`get_registration_manifest` — currently builds a plain dict), `:168-184` (`register()`); `sdk/tests/test_client_context.py:56-83` (register-with-mock-redis test style — existing tests assert `hset`/`set` but never assert `xadd` NOT called, so they keep passing).

**Files:**
- Modify: `sdk/alfred_sdk/client.py`
- Create: `sdk/tests/test_client_register.py`

**Interfaces:**
- Consumes: `CredentialSchema`, `ServiceManifest` (Task 1); SDK `ServiceRegistered` (Task 3).
- Produces (contract C1 + C2):
  - `AlfredClient.__init__(..., credentials_schema: CredentialSchema | None = None, credentials_endpoint: str | None = None)`; stored as `self.credentials_schema` / `self.credentials_endpoint`.
  - `AlfredClient.EVENTS_STREAM = "alfred:events"` class constant (duplicated from `shared.streams.EVENTS_STREAM` — SDK is standalone).
  - `get_registration_manifest() -> dict[str, Any]` now returns `ServiceManifest(...).model_dump()` including `credentials_schema` (dict or `None`) and `credentials_endpoint`.
  - `register()` publishes `ServiceRegistered` to `alfred:events` with wire format `{"event": event.model_dump_json()}` immediately AFTER the registry hset, with `source = service_name`.
- Consumed by: Task 5/6/7 (core reads the manifest keys and the event), Plan 2 (home-service passes both params).

- [ ] **Step 1: Write the failing tests**

Create `sdk/tests/test_client_register.py`:

```python
"""Tests for AlfredClient credential declaration + ServiceRegistered publication."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from sdk.alfred_sdk.client import AlfredClient
from sdk.alfred_sdk.events import ServiceRegistered
from sdk.alfred_sdk.feature import CredentialField, CredentialSchema

HA_SCHEMA = CredentialSchema(
    fields={
        "url": CredentialField(
            label="Home Assistant URL",
            field_type="url",
            default="http://homeassistant.local:8123",
        ),
        "token": CredentialField(label="Access Token", field_type="password"),
    }
)


def _mock_redis() -> AsyncMock:
    mock = AsyncMock()
    mock.hset = AsyncMock()
    mock.xadd = AsyncMock()
    mock.set = AsyncMock()
    mock.aclose = AsyncMock()
    return mock


def test_client_stores_credential_config() -> None:
    client = AlfredClient(
        service_name="home-service",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    assert client.credentials_schema is HA_SCHEMA
    assert client.credentials_endpoint == "http://localhost:8000/credentials"


def test_client_credential_config_defaults_none() -> None:
    client = AlfredClient(service_name="plain-service")
    assert client.credentials_schema is None
    assert client.credentials_endpoint is None


def test_manifest_includes_credentials() -> None:
    client = AlfredClient(
        service_name="home-service",
        service_endpoint="http://localhost:8000/mcp",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    manifest = client.get_registration_manifest()
    assert manifest["service_name"] == "home-service"
    assert manifest["credentials_endpoint"] == "http://localhost:8000/credentials"
    assert manifest["credentials_schema"]["fields"]["token"]["field_type"] == "password"


def test_manifest_defaults_to_no_credentials() -> None:
    client = AlfredClient(service_name="plain-service")
    manifest = client.get_registration_manifest()
    assert manifest["credentials_schema"] is None
    assert manifest["credentials_endpoint"] is None


@pytest.mark.asyncio
async def test_register_publishes_service_registered_after_hset() -> None:
    mock_redis = _mock_redis()
    call_order: list[str] = []
    mock_redis.hset.side_effect = lambda *a, **k: call_order.append("hset")
    mock_redis.xadd.side_effect = lambda *a, **k: call_order.append("xadd")

    client = AlfredClient(
        service_name="home-service",
        credentials_schema=HA_SCHEMA,
        credentials_endpoint="http://localhost:8000/credentials",
    )
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    assert call_order == ["hset", "xadd"]

    args: tuple[Any, ...] = mock_redis.xadd.call_args[0]
    stream, payload = args
    assert stream == "alfred:events"
    event = ServiceRegistered.model_validate_json(payload["event"])
    assert event.source == "home-service"
    assert event.service_name == "home-service"
    assert event.credentials_endpoint == "http://localhost:8000/credentials"
    assert event.has_credentials_schema is True


@pytest.mark.asyncio
async def test_register_publishes_even_without_credentials() -> None:
    mock_redis = _mock_redis()
    client = AlfredClient(service_name="plain-service")
    with patch("redis.asyncio.from_url", return_value=mock_redis):
        await client.register()

    payload = mock_redis.xadd.call_args[0][1]
    event = ServiceRegistered.model_validate_json(payload["event"])
    assert event.service_name == "plain-service"
    assert event.has_credentials_schema is False
    assert event.credentials_endpoint is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest sdk/tests/test_client_register.py -q`
Expected: FAIL — `TypeError: AlfredClient.__init__() got an unexpected keyword argument 'credentials_schema'`

- [ ] **Step 3: Implement the client changes**

In `sdk/alfred_sdk/client.py`:

1. Add `CredentialSchema` to the `TYPE_CHECKING` block (lines 13-17):

```python
if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from .feature import BaseFeature, CredentialSchema
```

2. Replace the class constants and `__init__` (lines 25-42) with:

```python
    REGISTRY_KEY = "alfred:tool_registry"  # must match ToolRegistry.REGISTRY_KEY in core/
    # Duplicated from shared.streams.EVENTS_STREAM — SDK must be standalone
    EVENTS_STREAM = "alfred:events"

    def __init__(
        self,
        service_name: str = "",
        service_endpoint: str = "",
        redis_url: str = "",
        mqtt_host: str = "",
        mqtt_port: int = 1883,
        credentials_schema: CredentialSchema | None = None,
        credentials_endpoint: str | None = None,
    ) -> None:
        self.service_name = service_name or os.getenv("ALFRED_SERVICE_NAME", "unknown")
        self.service_endpoint = service_endpoint or os.getenv("ALFRED_SERVICE_ENDPOINT", "")
        self.redis_url = redis_url or os.getenv("REDIS_URL", "redis://localhost:6379")
        self.mqtt_host = mqtt_host or os.getenv("MQTT_HOST", "localhost")
        self.mqtt_port = mqtt_port
        self.credentials_schema = credentials_schema
        self.credentials_endpoint = credentials_endpoint

        self._tool_fns: dict[str, Callable[..., Any]] = {}
        self._features: list[BaseFeature] = []
```

3. Replace `get_registration_manifest` (lines 156-166) with:

```python
    def get_registration_manifest(self) -> dict[str, Any]:
        """Build the tool registration manifest for Alfred's registry."""
        from .feature import ServiceManifest

        manifest = ServiceManifest(
            service_name=self.service_name,
            service_endpoint=self.service_endpoint,
            features=[f.to_manifest() for f in self._features],
            credentials_schema=self.credentials_schema,
            credentials_endpoint=self.credentials_endpoint,
        )
        return manifest.model_dump()
```

4. Replace `register` (lines 168-184) with:

```python
    async def register(self) -> None:
        """Register this service's tools and context with Alfred's registry on Redis.

        Publishes a ServiceRegistered event to alfred:events AFTER the registry
        hset — consumers read the manifest from the registry when handling the
        event, so ordering matters.
        """
        import json

        import redis.asyncio as aioredis

        from .events import ServiceRegistered

        r: aioredis.Redis = aioredis.from_url(self.redis_url)
        try:
            manifest = self.get_registration_manifest()
            await r.hset(self.REGISTRY_KEY, self.service_name, json.dumps(manifest))  # type: ignore[misc]

            event = ServiceRegistered(
                source=self.service_name,
                service_name=self.service_name,
                credentials_endpoint=self.credentials_endpoint,
                has_credentials_schema=self.credentials_schema is not None,
            )
            await r.xadd(self.EVENTS_STREAM, {"event": event.model_dump_json()})

            context = await self._collect_context()
            if context.controllable or context.sensors:
                context_key = f"{self.CONTEXT_KEY_PREFIX}{self.service_name}"
                await r.set(context_key, context.model_dump_json(), ex=600)
        finally:
            await r.aclose()
```

- [ ] **Step 4: Run the tests to verify they pass (including existing register tests)**

Run: `.venv/bin/python -m pytest sdk/ -q`
Expected: PASS — `test_client_register.py` green; pre-existing `test_client_context.py`/`test_client_features.py` register tests still green (they assert `hset`/`set` calls and manifest contents; the added `xadd` does not affect them).

- [ ] **Step 5: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict sdk/
git add sdk/alfred_sdk/client.py sdk/tests/test_client_register.py
git commit -m "feat(sdk): AlfredClient declares credentials and publishes ServiceRegistered on register

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Core service-credential helpers (`core/channels/service_credentials.py`)

**Context (read first):** `core/reflex/tool_registry.py:44-81` (permissive manifest parsing precedent), `shared/secrets.py` (`aget_all_secrets` keyring API — namespace is the first arg), `shared/streams.py` (`TOOL_REGISTRY_KEY`, `EVENTS_STREAM`, `decode_stream_value`), `core/integrations/base.py:35-50` (core `CredentialSchema` used to parse the manifest JSON), `conftest.py:37-40` (autouse `InMemoryKeyring` — tests can call `set_secret` freely).

**Files:**
- Create: `core/channels/service_credentials.py`
- Create: `tests/core/channels/test_service_credentials.py`
- Modify: `tests/core/channels/conftest.py` (add `home_service_manifest` fixture)

**Interfaces:**
- Consumes: `TOOL_REGISTRY_KEY`, `EVENTS_STREAM`, `decode_stream_value` from `shared.streams`; `aget_all_secrets` from `shared.secrets`; `CredentialSchema` from `core.integrations.base`; `AioRedis` from `shared.types`.
- Produces (used by Tasks 6 and 7):
  - `CREDENTIAL_PUSH_GROUP: str = "channels-credentials"`
  - `async def list_service_manifests(redis: AioRedis) -> dict[str, dict[str, Any]]` — all registry manifests with a valid `credentials_schema`
  - `async def get_service_manifest(redis: AioRedis, name: str) -> dict[str, Any] | None`
  - `def parse_schema(manifest: dict[str, Any]) -> CredentialSchema`
  - `def validate_credential_body(schema: CredentialSchema, body: dict[str, str]) -> None` — raises `HTTPException(422)`
  - `async def build_service_info(name: str, manifest: dict[str, Any]) -> dict[str, Any]` — GET entry (`kind="service"`, `category="service"`)
  - `async def stored_pushable_credentials(name: str, schema: CredentialSchema) -> dict[str, str] | None` — stored non-transient fields, or `None` if any required non-transient field is missing
  - `async def push_credentials(http: httpx.AsyncClient, endpoint: str, fields: dict[str, str]) -> None` — POST flat JSON (contract C4); raises `httpx.HTTPError` on failure
  - `def service_payload_healthy(status_code: int, payload: dict[str, Any]) -> bool`

- [ ] **Step 1: Add the shared manifest fixture**

Append to `tests/core/channels/conftest.py`:

```python
@pytest.fixture
def home_service_manifest() -> dict[str, object]:
    """A registry manifest for a sovereign service with credential support.

    Mirrors what AlfredClient.get_registration_manifest() writes to
    alfred:tool_registry for home-service (Plan 2 declares exactly this schema).
    """
    return {
        "service_name": "home-service",
        "service_endpoint": "http://localhost:8000/mcp",
        "features": [],
        "credentials_schema": {
            "fields": {
                "url": {
                    "label": "Home Assistant URL",
                    "field_type": "url",
                    "required": True,
                    "placeholder": "",
                    "default": "http://homeassistant.local:8123",
                    "help_text": "",
                    "transient": False,
                },
                "token": {
                    "label": "Access Token",
                    "field_type": "password",
                    "required": True,
                    "placeholder": "",
                    "default": "",
                    "help_text": "Long-lived access token from your HA profile page",
                    "transient": False,
                },
            }
        },
        "credentials_endpoint": "http://localhost:8000/credentials",
    }
```

- [ ] **Step 2: Write the failing tests**

Create `tests/core/channels/test_service_credentials.py`:

```python
"""Tests for core/channels/service_credentials.py — helpers (worker tests come later)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from core.channels.service_credentials import (
    build_service_info,
    get_service_manifest,
    list_service_manifests,
    parse_schema,
    push_credentials,
    service_payload_healthy,
    stored_pushable_credentials,
    validate_credential_body,
)

# ── registry reads ──


@pytest.mark.asyncio
async def test_list_service_manifests_filters_and_survives_garbage(
    home_service_manifest: dict[str, Any],
) -> None:
    plain = {
        "service_name": "plain",
        "service_endpoint": "http://x/mcp",
        "features": [],
        "credentials_schema": None,
        "credentials_endpoint": None,
    }
    redis = AsyncMock()
    redis.hgetall = AsyncMock(
        return_value={
            b"home-service": json.dumps(home_service_manifest).encode(),
            b"plain": json.dumps(plain).encode(),
            b"broken": b"{not json",
        }
    )
    manifests = await list_service_manifests(redis)
    assert set(manifests) == {"home-service"}
    assert manifests["home-service"]["credentials_endpoint"] == "http://localhost:8000/credentials"


@pytest.mark.asyncio
async def test_get_service_manifest_found(home_service_manifest: dict[str, Any]) -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=json.dumps(home_service_manifest).encode())
    manifest = await get_service_manifest(redis, "home-service")
    assert manifest is not None
    assert manifest["service_name"] == "home-service"


@pytest.mark.asyncio
async def test_get_service_manifest_none_for_missing() -> None:
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=None)
    assert await get_service_manifest(redis, "nope") is None


@pytest.mark.asyncio
async def test_get_service_manifest_none_without_schema() -> None:
    plain = {"service_name": "plain", "service_endpoint": "http://x/mcp", "features": []}
    redis = AsyncMock()
    redis.hget = AsyncMock(return_value=json.dumps(plain).encode())
    assert await get_service_manifest(redis, "plain") is None


# ── validation ──


def test_validate_credential_body_rejects_unknown(home_service_manifest: dict[str, Any]) -> None:
    schema = parse_schema(home_service_manifest)
    with pytest.raises(HTTPException) as exc_info:
        validate_credential_body(schema, {"url": "http://x", "token": "t", "bogus": "v"})
    assert exc_info.value.status_code == 422


def test_validate_credential_body_rejects_missing_required(
    home_service_manifest: dict[str, Any],
) -> None:
    schema = parse_schema(home_service_manifest)
    with pytest.raises(HTTPException) as exc_info:
        validate_credential_body(schema, {"url": "http://x"})
    assert exc_info.value.status_code == 422


def test_validate_credential_body_accepts_complete(home_service_manifest: dict[str, Any]) -> None:
    schema = parse_schema(home_service_manifest)
    validate_credential_body(schema, {"url": "http://x", "token": "t"})  # no raise


# ── GET entry shape (contract C5) ──


@pytest.mark.asyncio
async def test_build_service_info_shape(home_service_manifest: dict[str, Any]) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    info = await build_service_info("home-service", home_service_manifest)
    assert info["name"] == "home-service"
    assert info["kind"] == "service"
    assert info["category"] == "service"
    assert info["configured"] == {"url": True, "token": False}
    assert info["schema"]["fields"]["token"]["field_type"] == "password"


# ── keyring completeness ──


@pytest.mark.asyncio
async def test_stored_pushable_credentials_requires_all_required(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    schema = parse_schema(home_service_manifest)
    assert await stored_pushable_credentials("home-service", schema) is None

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    assert await stored_pushable_credentials("home-service", schema) is None  # token missing

    set_secret("home-service", "token", "tok")
    assert await stored_pushable_credentials("home-service", schema) == {
        "url": "http://192.168.50.159:8123",
        "token": "tok",
    }


# ── push (contract C4) ──


@pytest.mark.asyncio
async def test_push_credentials_posts_flat_json() -> None:
    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "health": {"status": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await push_credentials(
            http, "http://localhost:8000/credentials", {"url": "u", "token": "t"}
        )
    assert seen == [{"url": "u", "token": "t"}]


@pytest.mark.asyncio
async def test_push_credentials_raises_on_error_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await push_credentials(http, "http://localhost:8000/credentials", {"url": "u"})


# ── health convention ──


def test_service_payload_healthy() -> None:
    connected = {
        "status": "ok",
        "service": "home-service",
        "ha": {"state": "connected", "entities": 87, "areas": 6, "last_event_age_s": 2.1},
    }
    assert service_payload_healthy(200, connected) is True
    assert service_payload_healthy(503, connected) is False
    assert service_payload_healthy(200, {"status": "error"}) is False
    assert service_payload_healthy(200, {"status": "ok"}) is True  # no components → healthy
    auth_failed = {"status": "ok", "ha": {"state": "auth_failed", "entities": 0}}
    assert service_payload_healthy(200, auth_failed) is False
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/channels/test_service_credentials.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.channels.service_credentials'`

- [ ] **Step 4: Implement the module (helpers only — the worker is Task 7)**

Create `core/channels/service_credentials.py`:

```python
"""Sovereign-service credential helpers + ServiceRegistered re-push worker.

Sovereign services (home-service, signal-bridge, ...) declare a
``credentials_schema`` and ``credentials_endpoint`` in their SDK registration
manifest (Redis hash ``alfred:tool_registry``). Core is the single credential
authority: fields are stored in the OS keyring (namespace = service name;
secrets never touch Redis or non-keyring disk) and pushed to the service's
``credentials_endpoint`` over the trusted network.

Self-healing: the channels process consumes ``ServiceRegistered`` events from
``alfred:events`` (consumer group ``channels-credentials``) and re-pushes
stored credentials whenever a service (re)registers — services keep
credentials in memory only and recover within one registration cycle after a
restart. Event-driven, no polling.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from core.integrations.base import CredentialSchema
from shared.secrets import aget_all_secrets
from shared.streams import TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

CREDENTIAL_PUSH_GROUP = "channels-credentials"


# ── registry reads ──


def _parse_manifest(name: str, raw: bytes | str) -> dict[str, Any] | None:
    """Decode one registry manifest; None (logged) if malformed or without a usable schema."""
    try:
        manifest: dict[str, Any] = json.loads(decode_stream_value(raw))
    except (TypeError, json.JSONDecodeError):
        logger.error("Invalid JSON in tool registry for service '{}'", name)
        return None
    schema_dict = manifest.get("credentials_schema")
    if not schema_dict:
        return None
    try:
        CredentialSchema.model_validate(schema_dict)
    except ValidationError:
        logger.error("Malformed credentials_schema in registry for service '{}'", name)
        return None
    return manifest


async def list_service_manifests(redis: AioRedis) -> dict[str, dict[str, Any]]:
    """All registry manifests that declare a valid credentials_schema, keyed by service name."""
    raw: dict[bytes | str, bytes | str] = await redis.hgetall(TOOL_REGISTRY_KEY)  # type: ignore[misc]
    manifests: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        name = key.decode() if isinstance(key, bytes) else key
        manifest = _parse_manifest(name, value)
        if manifest is not None:
            manifests[name] = manifest
    return manifests


async def get_service_manifest(redis: AioRedis, name: str) -> dict[str, Any] | None:
    """One registry manifest; None if absent, malformed, or without a credentials_schema."""
    raw = await redis.hget(TOOL_REGISTRY_KEY, name)  # type: ignore[misc]
    if raw is None:
        return None
    return _parse_manifest(name, raw)


def parse_schema(manifest: dict[str, Any]) -> CredentialSchema:
    """Parse a manifest's credentials_schema into the core CredentialSchema model."""
    return CredentialSchema.model_validate(manifest["credentials_schema"])


# ── validation (shared by adapter + service PUT paths) ──


def validate_credential_body(schema: CredentialSchema, body: dict[str, str]) -> None:
    """Reject unknown fields and missing required non-transient fields (HTTP 422)."""
    unknown = set(body) - set(schema.fields)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields: {unknown}")
    missing = [
        f
        for f, field in schema.fields.items()
        if field.required and f not in body and not field.transient
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required fields: {missing}")


# ── GET /api/integrations entry (contract C5) ──


async def build_service_info(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a merged-integrations entry for a registry-declared service."""
    schema = parse_schema(manifest)
    stored = await aget_all_secrets(name, list(schema.fields))
    return {
        "name": name,
        "category": "service",
        "kind": "service",
        "schema": schema.model_dump(),
        "configured": {f: f in stored for f in schema.fields},
    }


# ── keyring + push (contract C4) ──


async def stored_pushable_credentials(
    name: str, schema: CredentialSchema
) -> dict[str, str] | None:
    """Stored non-transient fields, or None unless every required one is present."""
    persistent = [f for f, spec in schema.fields.items() if not spec.transient]
    stored = await aget_all_secrets(name, persistent)
    required = [f for f, spec in schema.fields.items() if spec.required and not spec.transient]
    if any(f not in stored for f in required):
        return None
    return stored


async def push_credentials(
    http: httpx.AsyncClient, endpoint: str, fields: dict[str, str]
) -> None:
    """POST credential fields as flat JSON to a service's credentials_endpoint.

    Raises httpx.HTTPError (connect failure or non-2xx) — callers decide policy.
    """
    response = await http.post(endpoint, json=fields)
    response.raise_for_status()


# ── health convention (GET status proxy) ──


def service_payload_healthy(status_code: int, payload: dict[str, Any]) -> bool:
    """Generic service-health convention — no service-specific keys in core.

    Healthy iff HTTP 200, top-level ``status == "ok"``, and every nested
    component dict that reports a ``state`` reports ``"connected"`` (e.g.
    home-service's ``ha.state``; see contract C6).
    """
    if status_code != 200 or payload.get("status") != "ok":
        return False
    return all(
        component.get("state") == "connected"
        for component in payload.values()
        if isinstance(component, dict) and "state" in component
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/core/channels/test_service_credentials.py -q`
Expected: PASS

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict core/
git add core/channels/service_credentials.py tests/core/channels/test_service_credentials.py \
    tests/core/channels/conftest.py
git commit -m "feat(core): sovereign-service credential helpers (registry reads, validation, push, health)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Merged integrations API (`GET`/`PUT`/`DELETE`/`status` for `kind=service`)

**Context (read first):** `core/channels/web_server.py:465-559` (the four existing integration endpoints), `:215-226` (`require_trusted_network` — TestClient host `"testclient"` is trusted), `:268-270` (lifespan sets `app.state.redis` and `app.state.http`; tests set both manually because `TestClient` without a `with` block does not run the lifespan — see `tests/core/channels/conftest.py:21-37`), `tests/core/channels/test_settings_api.py` (existing endpoint test style; its `web_client` fixture must keep passing).

**Files:**
- Modify: `core/channels/web_server.py:465-559`
- Modify: `tests/core/channels/conftest.py:24-31` (mock `hget` → `None` so unknown-integration 404 tests still pass — an unconfigured `AsyncMock.hget` would return a `MagicMock`, not `None`)
- Create: `tests/core/channels/test_service_integrations_api.py`

**Interfaces:**
- Consumes (Task 5): `list_service_manifests`, `get_service_manifest`, `parse_schema`, `validate_credential_body`, `build_service_info`, `push_credentials`, `service_payload_healthy`.
- Produces (contract C5, exact):
  - `GET /api/integrations` — every item carries `"kind": "adapter" | "service"`; service entries have `name` = registry service_name, `category` = `"service"`, `schema` = registry credentials_schema, `configured` computed from keyring.
  - `PUT /api/integrations/{name}/credentials` (kind=service, trusted network): validate → keyring store (non-transient) → POST full body to `credentials_endpoint` → `{"status": "ok", "pushed": true}`. No endpoint declared → `{"status": "ok", "pushed": false}`. Push failure → HTTP 502 with detail, keyring write persists (re-pushed on next `ServiceRegistered`).
  - `DELETE /api/integrations/{name}/credentials` (kind=service): clear keyring fields → `{"status": "ok"}`.
  - `GET /api/integrations/{name}/status` (kind=service): proxy `GET <service>/health` (URL = `urljoin(credentials_endpoint, "/health")`), return `{"name": name, "healthy": bool, "detail": <health payload>}`; unreachable → `healthy: false`, `detail: {"error": "..."}`.
  - Unknown name (neither adapter nor registry service) → 404 on all three mutating/status routes.

- [ ] **Step 1: Update the shared web fixture**

In `tests/core/channels/conftest.py`, inside the `web_client` fixture, add one line right after `mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)`:

```python
    mock_redis.hget = AsyncMock(return_value=None)
```

(The merged endpoints now consult the tool registry when an adapter name is unknown; an unset `AsyncMock` attribute would return a truthy `MagicMock` and break the existing 404 tests.)

- [ ] **Step 2: Write the failing tests**

Create `tests/core/channels/test_service_integrations_api.py`:

```python
"""Tests for the merged integrations API — registry-declared sovereign services.

Contract C5: adapters (IntegrationRegistry) and sovereign services
(alfred:tool_registry manifests with a credentials_schema) share the same
/api/integrations surface; service entries are marked kind="service".
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app
from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry
from shared.streams import AUTH_SESSION_PREFIX, TOOL_REGISTRY_KEY

_TEST_SESSION_ID = "test-auth-session"
_AUTH_SESSION_DATA: dict[bytes, bytes] = {
    b"authenticated": b"1",
    b"credential_id": b"test-cred",
    b"created_at": b"2026-04-16T00:00:00",
}


class _KindAdapter(Integration):
    """Minimal in-process adapter to verify kind='adapter' marking."""

    name = "kind_adapter"
    category = "testing"
    credentials_schema = CredentialSchema(fields={"key": CredentialField(label="Key")})

    def __init__(self, key: str = "") -> None:
        self.key = key

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return True


class _ServiceHttpHandler:
    """Programmable fake sovereign service for httpx.MockTransport."""

    def __init__(self) -> None:
        self.pushes: list[dict[str, str]] = []
        self.push_fails = False
        self.unreachable = False
        self.health: dict[str, Any] = {
            "status": "ok",
            "service": "home-service",
            "ha": {"state": "connected", "entities": 87, "areas": 6, "last_event_age_s": 2.1},
        }

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.unreachable:
            raise httpx.ConnectError("connection refused")
        if request.url.path == "/credentials":
            if self.push_fails:
                return httpx.Response(500, json={"detail": "boom"})
            self.pushes.append(json.loads(request.content))
            return httpx.Response(200, json={"status": "ok", "health": self.health})
        if request.url.path == "/health":
            return httpx.Response(200, json=self.health)
        return httpx.Response(404)


@pytest.fixture
def service_handler() -> _ServiceHttpHandler:
    return _ServiceHttpHandler()


@pytest.fixture
def service_client(
    service_handler: _ServiceHttpHandler, home_service_manifest: dict[str, Any]
) -> TestClient:
    """TestClient with home-service in a mocked tool registry + fake service HTTP."""
    registry_data = {b"home-service": json.dumps(home_service_manifest).encode()}

    mock_redis = AsyncMock()

    async def _fake_hgetall(key: str) -> dict[bytes, bytes]:
        if key == f"{AUTH_SESSION_PREFIX}{_TEST_SESSION_ID}":
            return _AUTH_SESSION_DATA
        if key == TOOL_REGISTRY_KEY:
            return registry_data
        return {}

    async def _fake_hget(key: str, field: str) -> bytes | None:
        if key == TOOL_REGISTRY_KEY:
            return registry_data.get(field.encode())
        return None

    mock_redis.hgetall = AsyncMock(side_effect=_fake_hgetall)
    mock_redis.hget = AsyncMock(side_effect=_fake_hget)

    app = create_app(redis_url="redis://localhost:6379")
    # create_app imports the real adapter modules (decorators register once per
    # session) — clear AFTER app creation so only test-controlled entries exist.
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["kind_adapter"] = _KindAdapter

    app.state.redis = mock_redis
    app.state.http = httpx.AsyncClient(transport=httpx.MockTransport(service_handler))
    client = TestClient(app)
    client.cookies.set("alfred_auth", _TEST_SESSION_ID)
    return client


# ── GET (merged listing) ──


def test_get_lists_service_entry(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations")
    assert resp.status_code == 200
    svc = next(e for e in resp.json() if e["name"] == "home-service")
    assert svc["kind"] == "service"
    assert svc["category"] == "service"
    assert set(svc["schema"]["fields"]) == {"url", "token"}
    assert svc["configured"] == {"url": False, "token": False}


def test_get_marks_adapters_with_kind(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations")
    adapter = next(e for e in resp.json() if e["name"] == "kind_adapter")
    assert adapter["kind"] == "adapter"
    assert adapter["category"] == "testing"


def test_get_service_configured_after_secret_stored(service_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    resp = service_client.get("/api/integrations")
    svc = next(e for e in resp.json() if e["name"] == "home-service")
    assert svc["configured"] == {"url": True, "token": False}


def test_get_never_returns_secret_values(service_client: TestClient) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "token", "super_secret_ha_token")
    resp = service_client.get("/api/integrations")
    assert "super_secret_ha_token" not in resp.text


# ── PUT (store + push) ──


def test_put_stores_and_pushes(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "pushed": True}

    from shared.secrets import get_secret

    assert get_secret("home-service", "url") == "http://192.168.50.159:8123"
    assert get_secret("home-service", "token") == "abc123"
    assert service_handler.pushes == [{"url": "http://192.168.50.159:8123", "token": "abc123"}]


def test_put_unknown_field_422(service_client: TestClient) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://x", "token": "t", "bogus": "v"},
    )
    assert resp.status_code == 422


def test_put_missing_required_422(service_client: TestClient) -> None:
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://x"},
    )
    assert resp.status_code == 422


def test_put_unreachable_service_502_keyring_persists(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.unreachable = True
    resp = service_client.put(
        "/api/integrations/home-service/credentials",
        json={"url": "http://192.168.50.159:8123", "token": "abc123"},
    )
    assert resp.status_code == 502

    from shared.secrets import get_secret

    # Keyring write persisted — the worker re-pushes on the next ServiceRegistered.
    assert get_secret("home-service", "token") == "abc123"


def test_put_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.put("/api/integrations/nonexistent/credentials", json={"x": "y"})
    assert resp.status_code == 404


# ── DELETE ──


def test_delete_service_credentials(service_client: TestClient) -> None:
    from shared.secrets import get_secret, set_secret

    set_secret("home-service", "url", "http://old")
    set_secret("home-service", "token", "old")
    resp = service_client.delete("/api/integrations/home-service/credentials")
    assert resp.status_code == 200
    assert get_secret("home-service", "url") is None
    assert get_secret("home-service", "token") is None


def test_delete_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.delete("/api/integrations/nonexistent/credentials")
    assert resp.status_code == 404


# ── status proxy ──


def test_status_proxies_health_connected(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/home-service/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "home-service"
    assert data["healthy"] is True
    assert data["detail"]["ha"]["state"] == "connected"


def test_status_unhealthy_on_auth_failed(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.health["ha"] = {
        "state": "auth_failed",
        "entities": 0,
        "areas": 0,
        "last_event_age_s": None,
    }
    resp = service_client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert data["detail"]["ha"]["state"] == "auth_failed"


def test_status_unreachable_service(
    service_client: TestClient, service_handler: _ServiceHttpHandler
) -> None:
    service_handler.unreachable = True
    resp = service_client.get("/api/integrations/home-service/status")
    data = resp.json()
    assert data["healthy"] is False
    assert "error" in data["detail"]


def test_status_unknown_name_404(service_client: TestClient) -> None:
    resp = service_client.get("/api/integrations/nonexistent/status")
    assert resp.status_code == 404
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/channels/test_service_integrations_api.py -q`
Expected: FAIL — service entries absent from GET (`StopIteration` in `next(...)`), PUT/DELETE/status return 404/500 for `home-service`.

- [ ] **Step 4: Implement the merged endpoints**

In `core/channels/web_server.py`, replace the four endpoint functions at lines 465-559 with the following (everything between the end of the `websocket_endpoint` function and `@app.post("/api/onboarding")`):

```python
    @app.get("/api/integrations")
    async def list_integrations() -> list[dict[str, Any]]:
        """List integration adapters + registry-declared sovereign services (C5)."""
        from core.channels.service_credentials import build_service_info, list_service_manifests
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import aget_all_secrets

        async def _build_info(name: str) -> dict[str, Any]:
            integration_cls = IntegrationRegistry.get_class(name)
            schema = integration_cls.credentials_schema
            stored = await aget_all_secrets(name, list(schema.fields))
            configured = {f: f in stored for f in schema.fields}
            return {
                "name": name,
                "category": integration_cls.category,
                "kind": "adapter",
                "schema": schema.model_dump(),
                "configured": configured,
            }

        adapters = list(
            await asyncio.gather(*[_build_info(n) for n in IntegrationRegistry.available()])
        )
        manifests = await list_service_manifests(app.state.redis)
        services = list(
            await asyncio.gather(*[build_service_info(n, m) for n, m in manifests.items()])
        )
        return adapters + services

    async def _save_service_credentials(name: str, body: dict[str, str]) -> dict[str, Any]:
        """Service branch of PUT: validate → keyring → push to credentials_endpoint."""
        from core.channels.service_credentials import (
            get_service_manifest,
            parse_schema,
            push_credentials,
            validate_credential_body,
        )
        from shared.secrets import aset_secret

        manifest = await get_service_manifest(app.state.redis, name)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")

        schema = parse_schema(manifest)
        validate_credential_body(schema, body)

        await asyncio.gather(
            *[aset_secret(name, f, v) for f, v in body.items() if not schema.fields[f].transient]
        )

        endpoint = manifest.get("credentials_endpoint")
        if not endpoint:
            return {"status": "ok", "pushed": False}
        try:
            # Push the full body (including transient fields) — the service
            # applies them live; only non-transient fields were persisted above.
            await push_credentials(app.state.http, endpoint, body)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=(
                    f"Credentials stored, but push to {name} failed: {exc}. "
                    "They will be re-pushed when the service re-registers."
                ),
            ) from exc
        return {"status": "ok", "pushed": True}

    @app.put(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_trusted_network)],
    )
    async def save_credentials(name: str, request: Request) -> dict[str, Any]:
        """Save credentials to the OS keyring (adapters + registry-declared services)."""
        from core.channels.service_credentials import validate_credential_body
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import aset_secret

        body: dict[str, str] = await request.json()

        try:
            integration_cls = IntegrationRegistry.get_class(name)
        except KeyError:
            return await _save_service_credentials(name, body)

        schema = integration_cls.credentials_schema
        validate_credential_body(schema, body)

        await asyncio.gather(
            *[aset_secret(name, f, v) for f, v in body.items() if not schema.fields[f].transient]
        )

        await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    @app.delete(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_trusted_network)],
    )
    async def delete_credentials(name: str) -> dict[str, str]:
        """Clear all credentials for an adapter or service from the OS keyring."""
        from core.channels.service_credentials import get_service_manifest, parse_schema
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import adelete_secret

        try:
            integration_cls = IntegrationRegistry.get_class(name)
            fields = list(integration_cls.credentials_schema.fields)
            is_adapter = True
        except KeyError:
            manifest = await get_service_manifest(app.state.redis, name)
            if manifest is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown integration: {name}"
                ) from None
            fields = list(parse_schema(manifest).fields)
            is_adapter = False

        await asyncio.gather(*[adelete_secret(name, f) for f in fields])

        if is_adapter:
            await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    async def _service_status(name: str) -> dict[str, Any]:
        """Service branch of status: proxy the service's /health (C5)."""
        from urllib.parse import urljoin

        from core.channels.service_credentials import get_service_manifest, service_payload_healthy

        manifest = await get_service_manifest(app.state.redis, name)
        if manifest is None:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")

        endpoint = manifest.get("credentials_endpoint") or manifest.get("service_endpoint") or ""
        if not endpoint:
            return {"name": name, "healthy": False, "detail": {"error": "no endpoint declared"}}
        health_url = urljoin(endpoint, "/health")
        try:
            resp = await app.state.http.get(health_url)
            payload: dict[str, Any] = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {"name": name, "healthy": False, "detail": {"error": str(exc)}}
        return {
            "name": name,
            "healthy": service_payload_healthy(resp.status_code, payload),
            "detail": payload,
        }

    @app.get("/api/integrations/{name}/status")
    async def integration_status(name: str) -> dict[str, Any]:
        """Health check for an adapter (in-process) or service (proxied /health)."""
        from core.integrations.registry import IntegrationRegistry

        try:
            IntegrationRegistry.get_class(name)
        except KeyError:
            return await _service_status(name)

        try:
            instance = IntegrationRegistry.get(name)
            healthy = await instance.health_check()
        except Exception:
            healthy = False
        return {"name": name, "healthy": healthy}
```

(The adapter PUT path now delegates its unknown/missing checks to `validate_credential_body` — identical logic to what it inlined before, so `test_settings_api.py` behavior is unchanged.)

- [ ] **Step 5: Run the new and pre-existing endpoint tests**

Run: `.venv/bin/python -m pytest tests/core/channels/ -q`
Expected: PASS — including all of `test_settings_api.py` (adapter behavior unchanged; unknown names now fall through to the registry, where the fixture's `hget → None` yields the same 404s).

- [ ] **Step 6: Lint, type-check, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict core/
git add core/channels/web_server.py tests/core/channels/conftest.py \
    tests/core/channels/test_service_integrations_api.py
git commit -m "feat(core): merged integrations API — registry services get kind=service, keyring PUT + push, /health status proxy

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: `credential_push_worker` — ServiceRegistered consumer + lifespan wiring

**Context (read first):** `core/notifications/delivery.py:24-68` (the channels-process consumer-worker pattern this copies: `ensure_consumer_group` → `xreadgroup` loop → ack → catch-all + 1s sleep), `core/reflex/runner.py:50-63` (`ensure_consumer_group`), `core/channels/web_server.py:263-326` (`_lifespan`: `delivery_task` start at 296-298, cancel at 315 — the new worker is wired identically), `tests/core/channels/test_web_server.py:246-307` (lifespan-test precedent with the exact patch set to reuse). Note: `alfred:events` also carries `TriggerFired`/`TriggerCreated` — the worker must filter on `event_type == "service_registered"` before validating.

**Files:**
- Modify: `core/channels/service_credentials.py` (append the worker)
- Modify: `core/channels/web_server.py` (`_lifespan`)
- Test: `tests/core/channels/test_service_credentials.py` (append)

**Interfaces:**
- Consumes: Task 5 helpers; `ServiceRegistered` from `bus.schemas.events`; `EVENTS_STREAM` from `shared.streams`; `ensure_consumer_group` from `core.reflex.runner`.
- Produces:
  - `async def credential_push_worker(redis: AioRedis, http: httpx.AsyncClient, consumer: str = "worker-1", shutdown: asyncio.Event | None = None) -> None` — consumer group `channels-credentials` on `alfred:events`; on each `ServiceRegistered`, if the keyring holds all required non-transient fields for that service, POSTs them to its `credentials_endpoint`. Push failures are logged (WARNING) and ACKed — the retry vehicle is the service's next `ServiceRegistered` (spec Section 4).
  - Lifespan wiring in the channels process (runs in every process that serves the web channel).

- [ ] **Step 1: Write the failing tests**

First, extend the top-of-file import block of `tests/core/channels/test_service_credentials.py` (do NOT put these mid-file — ruff E402):

```python
import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi import HTTPException

from bus.schemas.events import ServiceRegistered, TriggerFired
from core.channels.service_credentials import (
    build_service_info,
    credential_push_worker,
    get_service_manifest,
    list_service_manifests,
    parse_schema,
    push_credentials,
    service_payload_healthy,
    stored_pushable_credentials,
    validate_credential_body,
)
from core.channels.web_server import create_app
from shared.streams import EVENTS_STREAM
```

Then append the tests:

```python
# ── credential_push_worker (ServiceRegistered consumer) ──


def _stream_entries(event_json: str) -> list[Any]:
    return [(EVENTS_STREAM.encode(), [(b"1-0", {b"event": event_json.encode()})])]


def _worker_redis(
    manifest: dict[str, Any] | None, entries: list[Any]
) -> tuple[AsyncMock, asyncio.Event]:
    """AsyncMock redis whose xreadgroup yields one batch, then stops the worker."""
    shutdown = asyncio.Event()
    redis = AsyncMock()
    redis.hget = AsyncMock(
        return_value=json.dumps(manifest).encode() if manifest is not None else None
    )
    redis.xgroup_create = AsyncMock()
    redis.xack = AsyncMock()

    async def fake_xreadgroup(*args: Any, **kwargs: Any) -> list[Any]:
        shutdown.set()
        return entries

    redis.xreadgroup = AsyncMock(side_effect=fake_xreadgroup)
    return redis, shutdown


def _service_registered_json() -> str:
    return ServiceRegistered(
        source="home-service",
        service_name="home-service",
        credentials_endpoint="http://localhost:8000/credentials",
        has_credentials_schema=True,
    ).model_dump_json()


@pytest.mark.asyncio
async def test_worker_re_pushes_stored_credentials(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    set_secret("home-service", "token", "tok")

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok", "health": {"status": "ok"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == [{"url": "http://192.168.50.159:8123", "token": "tok"}]
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_skips_when_credentials_incomplete(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")  # token missing

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == []
    redis.xack.assert_awaited_once()  # still acked — nothing to retry until user saves


@pytest.mark.asyncio
async def test_worker_ignores_other_event_types(home_service_manifest: dict[str, Any]) -> None:
    fired = TriggerFired(trigger_id="t1", trigger_name="test", trigger_type="time")
    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(fired.model_dump_json())
    )

    pushes: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        pushes.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "ok"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)

    assert pushes == []
    redis.xack.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_push_failure_logged_and_acked(
    home_service_manifest: dict[str, Any],
) -> None:
    from shared.secrets import set_secret

    set_secret("home-service", "url", "http://192.168.50.159:8123")
    set_secret("home-service", "token", "tok")

    redis, shutdown = _worker_redis(
        home_service_manifest, _stream_entries(_service_registered_json())
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await credential_push_worker(redis, http, shutdown=shutdown)  # must not raise

    redis.xack.assert_awaited_once()


def test_lifespan_starts_credential_push_worker() -> None:
    """The channels lifespan starts the ServiceRegistered consumer (same wiring
    as the notification delivery worker). Patch set mirrors
    tests/core/channels/test_web_server.py::test_auth_status_not_shadowed_by_spa_catch_all.
    """
    from fastapi.testclient import TestClient

    calls: list[tuple[Any, Any]] = []

    async def fake_worker(
        redis: Any,
        http: Any,
        consumer: str = "worker-1",
        shutdown: asyncio.Event | None = None,
    ) -> None:
        calls.append((redis, http))

    mock_redis = AsyncMock()
    mock_redis.hgetall = AsyncMock(return_value={})
    mock_redis.close = AsyncMock()

    mock_store = AsyncMock()
    mock_store.initialize = AsyncMock()
    mock_store.close = AsyncMock()
    mock_store.get_user_id = AsyncMock(return_value=None)
    mock_store.list_credentials = AsyncMock(return_value=[])
    mock_store.has_any_credential = AsyncMock(return_value=False)

    with (
        patch("core.channels.web_server.aioredis.from_url", return_value=mock_redis),
        patch("core.channels.web_server.CredentialStore", return_value=mock_store),
        patch("core.channels.web_server._init_apns_adapter", new=AsyncMock()),
        patch(
            "core.notifications.delivery.notification_delivery_worker",
            new=AsyncMock(return_value=None),
        ),
        patch("core.channels.service_credentials.credential_push_worker", new=fake_worker),
        patch("httpx.AsyncClient.aclose", new=AsyncMock()),
    ):
        app = create_app(redis_url="redis://localhost:6379")
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200

    assert len(calls) == 1
    assert calls[0][0] is mock_redis  # worker gets the shared pool
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/core/channels/test_service_credentials.py -q`
Expected: FAIL — `ImportError: cannot import name 'credential_push_worker'`

- [ ] **Step 3: Implement the worker**

Append to `core/channels/service_credentials.py`:

```python
# ── ServiceRegistered re-push worker ──


async def _handle_event_entry(
    redis: AioRedis,
    http: httpx.AsyncClient,
    entry_data: dict[bytes | str, bytes | str],
) -> None:
    """Process one alfred:events entry; push credentials for ServiceRegistered."""
    raw = entry_data.get("event") or entry_data.get(b"event")
    if raw is None:
        return

    try:
        payload = json.loads(decode_stream_value(raw))
    except json.JSONDecodeError:
        return
    # alfred:events also carries TriggerFired/TriggerCreated — only act on ours.
    if payload.get("event_type") != "service_registered":
        return

    event = ServiceRegistered.model_validate(payload)
    if not event.has_credentials_schema or event.credentials_endpoint is None:
        return

    manifest = await get_service_manifest(redis, event.service_name)
    if manifest is None:
        logger.warning(
            "ServiceRegistered for '{}' but no registry manifest with a schema",
            event.service_name,
        )
        return

    fields = await stored_pushable_credentials(event.service_name, parse_schema(manifest))
    if fields is None:
        logger.info("No stored credentials for '{}' — skipping push", event.service_name)
        return

    try:
        await push_credentials(http, event.credentials_endpoint, fields)
        logger.info(
            "Re-pushed credentials to '{}' at {}",
            event.service_name,
            event.credentials_endpoint,
        )
    except httpx.HTTPError as exc:
        # ACKed by the caller regardless — the retry vehicle is the service's
        # next ServiceRegistered (it re-registers on restart / re-connect).
        logger.warning("Credential push to '{}' failed: {}", event.service_name, exc)


async def credential_push_worker(
    redis: AioRedis,
    http: httpx.AsyncClient,
    consumer: str = "worker-1",
    shutdown: asyncio.Event | None = None,
) -> None:
    """Consume ServiceRegistered from alfred:events and re-push stored credentials.

    Runs in the channels process with its own consumer group (contract C5:
    ``channels-credentials``). Same worker pattern as
    core/notifications/delivery.py::notification_delivery_worker.
    """
    await ensure_consumer_group(redis, EVENTS_STREAM, CREDENTIAL_PUSH_GROUP)
    _shutdown = shutdown or asyncio.Event()

    while not _shutdown.is_set():
        try:
            entries: list[
                tuple[
                    bytes | str,
                    list[tuple[bytes | str, dict[bytes | str, bytes | str]]],
                ]
            ] = await redis.xreadgroup(  # type: ignore[misc,unused-ignore]
                CREDENTIAL_PUSH_GROUP, consumer, {EVENTS_STREAM: ">"}, count=10, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    await _handle_event_entry(redis, http, entry_data)
                    await redis.xack(EVENTS_STREAM, CREDENTIAL_PUSH_GROUP, entry_id)

        except Exception as e:
            if not _shutdown.is_set():
                logger.error("Credential push worker error: {}", e)
                await asyncio.sleep(1)
```

And extend the module's imports (top of file) to:

```python
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from bus.schemas.events import ServiceRegistered
from core.integrations.base import CredentialSchema
from core.reflex.runner import ensure_consumer_group
from shared.secrets import aget_all_secrets
from shared.streams import EVENTS_STREAM, TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis
```

- [ ] **Step 4: Wire the worker into the channels lifespan**

In `core/channels/web_server.py` `_lifespan`, replace the delivery-task block (lines 295-298):

```python
    shutdown = asyncio.Event()
    delivery_task = asyncio.create_task(
        notification_delivery_worker(pool, group="channels-delivery", shutdown=shutdown)
    )
```

with:

```python
    from core.channels.service_credentials import credential_push_worker

    shutdown = asyncio.Event()
    delivery_task = asyncio.create_task(
        notification_delivery_worker(pool, group="channels-delivery", shutdown=shutdown)
    )
    credential_push_task = asyncio.create_task(
        credential_push_worker(pool, app.state.http, shutdown=shutdown)
    )
```

and replace the shutdown block (lines 314-316):

```python
    shutdown.set()
    delivery_task.cancel()
    warmup_task.cancel()
```

with:

```python
    shutdown.set()
    delivery_task.cancel()
    credential_push_task.cancel()
    warmup_task.cancel()
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/core/channels/ -q`
Expected: PASS (worker tests, lifespan test, and all pre-existing channel tests)

- [ ] **Step 6: Lint, type-check, full test run, commit**

```bash
ruff check . --fix && ruff format .
.venv/bin/python -m mypy --strict core/
.venv/bin/python -m pytest -x -q
git add core/channels/service_credentials.py core/channels/web_server.py \
    tests/core/channels/test_service_credentials.py
git commit -m "feat(core): channels-credentials consumer re-pushes keyring credentials on ServiceRegistered

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: SPA — `kind` field + "external service" badge

**Context (read first):** `web/src/lib/types.ts:43-51` (`CredentialField`/`IntegrationInfo`), `web/src/pages/IntegrationCard.tsx:88-167` (card header — the schema-driven form already renders any credential schema, so services need zero form changes), `web/src/pages/SettingsPage.test.tsx` + `web/src/pages/IntegrationCard.test.tsx` (vitest + testing-library patterns; `npm run test` = `vitest run`). Note `name.replace(/_/g, " ")` only replaces underscores — `home-service` renders as `HOME-SERVICE`. The onboarding wizard (`web/src/pages/OnboardingPage.tsx`) renders `IntegrationCard` from the same `/api/integrations` query, so the HA card appears in onboarding automatically — no onboarding changes needed (spec Section 1 requirement satisfied for free).

**Files:**
- Modify: `web/src/lib/types.ts:47-51`
- Modify: `web/src/pages/IntegrationCard.tsx:97,157-167`
- Test: `web/src/pages/IntegrationCard.test.tsx`
- Test: `web/src/pages/SettingsPage.test.tsx`

**Interfaces:**
- Consumes: `GET /api/integrations` items now carry `kind: "adapter" | "service"` (Task 6).
- Produces: `IntegrationInfo.kind?: "adapter" | "service"` (optional — older payloads and existing test fixtures without `kind` stay valid); `IntegrationCard` renders an outline `Badge` with text `external service` when `kind === "service"`.

- [ ] **Step 1: Write the failing tests**

Append to `web/src/pages/IntegrationCard.test.tsx` (inside the existing `describe("IntegrationCard", ...)` block), and add the fixture below the existing `WEATHER_INTEGRATION` fixture:

```tsx
const HOME_SERVICE_INTEGRATION: IntegrationInfo = {
  name: "home-service",
  category: "service",
  kind: "service",
  schema: {
    fields: {
      url: {
        label: "Home Assistant URL",
        field_type: "url",
        required: true,
        placeholder: "",
        default: "http://homeassistant.local:8123",
        help_text: "",
        transient: false,
      },
      token: {
        label: "Access Token",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "Long-lived access token from your HA profile page",
        transient: false,
      },
    },
  },
  configured: { url: false, token: false },
};
```

```tsx
  it("renders an external service badge and schema-driven fields for kind=service", async () => {
    renderCard({ integration: HOME_SERVICE_INTEGRATION });
    await waitFor(() => expect(screen.getByText("HOME-SERVICE")).toBeInTheDocument());
    expect(screen.getByText("external service")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant URL")).toBeInTheDocument();
    expect(screen.getByText("Access Token")).toBeInTheDocument();
    // Unconfigured field pre-fills its schema default.
    expect(screen.getByDisplayValue("http://homeassistant.local:8123")).toBeInTheDocument();
  });

  it("does not render the service badge for adapters", async () => {
    renderCard();
    await waitFor(() => expect(screen.getByText("WEATHER")).toBeInTheDocument());
    expect(screen.queryByText("external service")).toBeNull();
  });
```

Append to `web/src/pages/SettingsPage.test.tsx` (inside the `describe("SettingsPage", ...)` block), and add this fixture after the existing `WEATHER` fixture:

```tsx
const HOME_SERVICE = {
  name: "home-service",
  category: "service",
  kind: "service",
  schema: {
    fields: {
      url: {
        label: "Home Assistant URL",
        field_type: "url",
        required: true,
        placeholder: "",
        default: "http://homeassistant.local:8123",
        help_text: "",
        transient: false,
      },
      token: {
        label: "Access Token",
        field_type: "password",
        required: true,
        placeholder: "",
        default: "",
        help_text: "",
        transient: false,
      },
    },
  },
  configured: { url: false, token: false },
};
```

```tsx
  it("renders service entries from the merged API alongside adapters", async () => {
    setupMocks([WEATHER, HOME_SERVICE]);
    renderPage();
    await waitFor(() => expect(screen.getByText("HOME-SERVICE")).toBeInTheDocument());
    expect(screen.getByText("WEATHER")).toBeInTheDocument();
    expect(screen.getByText("external service")).toBeInTheDocument();
    expect(screen.getByText("Home Assistant URL")).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npm run test`
Expected: FAIL — TypeScript error on `kind` (not in `IntegrationInfo`) and/or `Unable to find an element with the text: external service`

- [ ] **Step 3: Implement**

In `web/src/lib/types.ts`, replace the `IntegrationInfo` interface (lines 47-51) with:

```ts
export interface IntegrationInfo {
  name: string; category: string;
  kind?: "adapter" | "service";
  schema: { fields: Record<string, CredentialField> };
  configured: Record<string, boolean>;
}
```

In `web/src/pages/IntegrationCard.tsx`:

1. Line 97 — destructure `kind`:

```tsx
  const { name, category, schema, configured, kind } = integration;
```

2. Replace the header `<div>` block (lines 158-163):

```tsx
        <div>
          <CardTitle className="font-mono text-xs tracking-widest">
            {name.replace(/_/g, " ").toUpperCase()}
          </CardTitle>
          <span className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
            {category}
            {kind === "service" && (
              <Badge variant="outline" className="text-[9px]">
                external service
              </Badge>
            )}
          </span>
        </div>
```

- [ ] **Step 4: Run the frontend suite to verify it passes**

Run: `cd web && npm run lint && npm run test && npm run build`
Expected: lint clean, all vitest suites PASS, `tsc -b && vite build` succeeds (emits `web/dist/`).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/types.ts web/src/pages/IntegrationCard.tsx \
    web/src/pages/IntegrationCard.test.tsx web/src/pages/SettingsPage.test.tsx
git commit -m "feat(web): kind=service badge on schema-driven integration cards

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Documentation (`docs/secrets.md`, CLAUDE.md notes)

**Context (read first):** `docs/secrets.md` (existing secrets doc this extends), `CLAUDE.md` "Secrets & Credentials" section and "Gotchas" list, `sdk/CLAUDE.md` "Key Patterns".

**Files:**
- Modify: `docs/secrets.md` (append section)
- Modify: `CLAUDE.md` ("Secrets & Credentials" section + one Gotcha)
- Modify: `sdk/CLAUDE.md` ("Key Patterns" section)

**Interfaces:** none (documentation only; content below is final copy).

- [ ] **Step 1: Append to `docs/secrets.md`**

```markdown
## Sovereign Service Credentials (kind=service)

Integration adapters run in-process; sovereign services (home-service,
signal-bridge, ...) are separate processes that declare credential needs at
registration time via the SDK:

- `AlfredClient(credentials_schema=..., credentials_endpoint=...)` embeds a
  `CredentialSchema` (field shape identical to `core/integrations/base.py`,
  guarded by `sdk/tests/test_schema_compatibility.py`) and an absolute
  `credentials_endpoint` URL in the `alfred:tool_registry` manifest.
- `AlfredClient.register()` publishes a `ServiceRegistered` event to
  `alfred:events` AFTER the registry hset.

Core stays the single credential authority (`core/channels/service_credentials.py`):

- `GET /api/integrations` merges adapters (`"kind": "adapter"`) with
  registry-declared services (`"kind": "service"`, `category` = `"service"`);
  the schema-driven `IntegrationCard` renders both with no special-casing.
- `PUT /api/integrations/{name}/credentials` (service, trusted network):
  validate against the registry schema → store non-transient fields in the OS
  keyring (namespace = service name) → POST the flat field dict to the
  service's `credentials_endpoint`. Push failure → HTTP 502, but the keyring
  write persists and is re-pushed on the service's next registration.
- `GET /api/integrations/{name}/status` (service) proxies the service's
  `/health`. Healthy iff HTTP 200, top-level `status == "ok"`, and every
  nested component dict with a `"state"` key reports `"connected"`.
- Self-healing re-push: the channels process consumes `ServiceRegistered`
  from `alfred:events` (consumer group `channels-credentials`) and re-pushes
  stored credentials — services keep credentials in memory only and recover
  automatically on restart. Event-driven; no polling.
```

- [ ] **Step 2: Update `CLAUDE.md`**

Append to the "Secrets & Credentials" section:

```markdown
- Sovereign services declare `credentials_schema`/`credentials_endpoint` via `AlfredClient`; `register()` publishes `ServiceRegistered` to `alfred:events` AFTER the registry hset
- `core/channels/service_credentials.py` — service credential helpers + `credential_push_worker` (consumer group `channels-credentials` on `alfred:events`) re-pushes keyring credentials whenever a service re-registers
- `GET /api/integrations` merges adapters (`kind: "adapter"`) and registry-declared services (`kind: "service"`); service PUT pushes to the service's `credentials_endpoint`, service status proxies its `/health`
```

Append to the "Gotchas" list:

```markdown
- Service credential push failures return HTTP 502 from PUT but the keyring write persists — recovery is event-driven via the next `ServiceRegistered`, never a retry loop
```

- [ ] **Step 3: Update `sdk/CLAUDE.md` "Key Patterns"**

Append:

```markdown
- `AlfredClient(credentials_schema=CredentialSchema(...), credentials_endpoint="http://host:port/credentials")` declares credential needs; core pushes stored values to that endpoint on every `register()`
- `client.register()` also publishes a `ServiceRegistered` event to `alfred:events` (constant duplicated as `AlfredClient.EVENTS_STREAM` — SDK stays standalone)
- `@tool(audience="reflex"|"conscious", risk="benign"|"elevated"|"critical")` — defaults `conscious`/`benign`; carried into `ToolManifest`
```

- [ ] **Step 4: Commit**

```bash
git add docs/secrets.md CLAUDE.md sdk/CLAUDE.md
git commit -m "docs: sovereign service credential flow (schema, push, re-push worker)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Final gate — full lint/type/test/build

**Files:** none (verification only).

**Interfaces:** none.

- [ ] **Step 1: Python gate**

```bash
ruff check . --fix
ruff format .
.venv/bin/python -m mypy --strict bus/ core/ domains/ evals/ runner/ sdk/ shared/ telemetry/
.venv/bin/python -m pytest -x -q
```

Expected: ruff clean (no remaining violations), mypy `Success: no issues found`, pytest all green (≥ 917 backend tests + the ~40 added by this plan), zero skips introduced by this work.

- [ ] **Step 2: Frontend gate**

```bash
cd web && npm run lint && npm run test && npm run build
```

Expected: eslint clean, vitest all green (105 pre-existing + 3 added), build emits `web/dist/`.

- [ ] **Step 3: Commit any gate fixups**

```bash
git add -A
git commit -m "chore: lint/format fixups from full gate

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Skip the commit if the gate produced no diff.)

- [ ] **Step 4: After-merge follow-ups (record, do not do now)**

- Plan 2 (home-service) passes `credentials_schema`/`credentials_endpoint` to its `AlfredClient` and implements `POST /credentials` + the extended `/health` (contracts C4/C6).
- Manual QA (post-merge, per QA Backlog convention): save real HA credentials via Settings → verify keyring entry, push, and re-push after restarting home-service.

---

## Self-Review (completed)

- **Spec coverage (Section 1):** SDK manifest extension → Tasks 1, 2, 4. `ServiceRegistered` on `alfred:events` after registry write → Tasks 3, 4. Merged `GET /api/integrations` with `kind` marking → Task 6. Service `PUT` (validate → keyring → push over trusted network) → Tasks 5, 6. Service `status` proxying `/health` → Tasks 5, 6. Self-healing re-push consumer with own consumer group in the channels process → Task 7. Settings/onboarding UI rendering via the existing schema-driven card + service badge → Task 8. Section 4's "failed push surfaces on the settings card and retries on next ServiceRegistered" → 502-with-persisted-keyring (Task 6) + ACK-and-wait worker policy (Task 7). home-service's own `POST /credentials`, `/health`, and schema declaration are Plan 2 (out of scope here by design).
- **Contract fidelity:** C1/C2 model shapes copied verbatim (Literal aliases `ToolAudience`/`ToolRisk` are the identical types); C2 wire format `{"event": model_dump_json()}` with `source = service_name`; C4 flat JSON push; C5 `kind`/`category`/`configured`/`pushed` shapes and the `channels-credentials` group name. One interpretation documented: C5's "pushed=false + 502 detail if service unreachable" is implemented as HTTP 502 (detail explains, keyring persists) so failures surface as error toasts through the untouched frontend `ApiError` path, while `pushed: false` with HTTP 200 is reserved for services that declare no `credentials_endpoint`.
- **Placeholder scan:** no TBD/TODO/"add validation later"; every code step contains complete code; every test step has full test code, exact run command, and expected outcome.
- **Type consistency:** `credential_push_worker(redis, http, consumer, shutdown)` matches between Task 7 impl, its tests, and the lifespan call; `build_service_info(name, manifest)`, `stored_pushable_credentials(name, schema)`, `validate_credential_body(schema, body)`, `service_payload_healthy(status_code, payload)` used identically in Tasks 5–7; `IntegrationInfo.kind` optional in TS matching Task 6's always-present backend field.
