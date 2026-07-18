# home-service Rewrite: Discovery, Generated Capabilities, State Ingest — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite home-service to discover the real Home Assistant over its WebSocket API, generate the full tool surface from HA's own registries (no hand-written features, no entity-ID guessing), forward every `state_changed` to MQTT `home/state_changed`, and accept credentials pushed at runtime via `POST /credentials`.

**Architecture:** A persistent `HAConnection` (WebSocket, auth handshake, message-id correlation, reconnect with backoff) feeds an `EntityIndex` (registries → friendly-name/area resolution) and a `CapabilityGenerator` (HA service catalog × EntityIndex → SDK `ToolMeta` tagged `audience`/`risk` from `config/risk_map.yaml`). A single generated `BaseFeature` binds dispatch handlers so the existing `/mcp` JSON-RPC contract is unchanged. A `StateForwarder` publishes every state change to MQTT for the existing alfred bridge → `alfred:home:state_changed` pipeline.

**Tech Stack:** Python 3.13, FastAPI, `websockets` (asyncio client + test server), `aiomqtt`, Pydantic v2, loguru, PyYAML, alfred-sdk (installed from `/Users/anirudhlath/code/private/alfred/alfred/sdk` source), pytest + pytest-asyncio, uv, ruff (line 100), mypy --strict.

**This is Plan 2 of 3.** Spec: `alfred/docs/superpowers/specs/2026-07-15-real-home-ha-integration-design.md` (Section 2 + the home-service side of Section 1). Contracts C1, C4, C6, C9, C11, C12 from the shared contracts document are FIXED — names/types/shapes below match them exactly.

## Global Constraints

- **Repo:** ALL implementation work happens in `/Users/anirudhlath/code/private/alfred/home-service` (its own git repo). File paths in tasks are relative to that repo root. Create the working worktree INSIDE `home-service/`, never at the workspace root.
- **Do NOT modify the alfred monorepo.** The bridge `maxlen` cap on `alfred:home:state_changed` and all core-side work belong to Plans 1 and 3. This plan only *reads* alfred code for contracts.
- **Plan 1 dependency (hard gate):** Plan 1 adds to alfred-sdk: `CredentialField`/`CredentialSchema` in `sdk/alfred_sdk/feature.py`, `audience`/`risk` on `ToolMeta`/`ToolManifest`, and `credentials_schema`/`credentials_endpoint` kwargs on `AlfredClient.__init__` (contract C1). Task 1 Step 3 verifies this and STOPS the plan if absent — do not work around it.
- **Python 3.13 only** — `uv venv --python 3.13`; modern syntax (match/case, `X | None`).
- **Package manager:** `uv` only. alfred-sdk is NOT on PyPI — always install it from source path in the same command: `uv pip install -e ".[dev]" /Users/anirudhlath/code/private/alfred/alfred/sdk`.
- **Logging:** loguru only (`from loguru import logger`), never stdlib `logging`. Loguru uses brace formatting: `logger.info("x={}", x)`.
- **Pydantic v2** for all data models. **Async-first** for all I/O. Type hints on every function signature.
- **Quality gates:** `ruff check .`, `ruff format .`, `mypy --strict app alfred_ext` must pass; tests via `uv run pytest`. Final gate in Task 10.
- **Commits:** conventional-commit style; every commit message body ends with:
  `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`
- **Wire contracts that must not drift:**
  - `/mcp` request/response stays `{method, params, id}` → `{id, result, error}` (Alfred's `HomeAgent` depends on it).
  - MQTT topic is exactly `home/state_changed`; payload is `StateChangedEvent.model_dump_json()` from `alfred_sdk.events` (the alfred bridge wraps it as `{"event": payload}` into `alfred:home:state_changed` — see alfred `bus/bridge.py:26-46`).
  - `POST /credentials` body is flat `{url, token}` (contract C4); response `{"status": "ok", "health": <C6 payload>}`; 422 on unknown/missing fields.
  - `/health` payload per contract C6 (shape verified in Task 9 tests).
- **Env vars:** `MQTT_HOST` (default `localhost`), `MQTT_PORT` (default `1883`), `HA_HOST`/`HA_TOKEN` (dev fallback only), `SERVICE_HOST` (default `localhost`), `REDIS_URL` (SDK).
- **No polling** in production code paths — event-driven listeners + debounced/coalesced tasks. (Test helpers may poll-wait.)
- **alfred-side docs** (`alfred/docs/home-service.md`, architecture diagrams) are Plan 3 / follow-up scope, not this plan.

---

## Verified HA WebSocket API message shapes (contract C12)

Verified 2026-07-15 against https://developers.home-assistant.io/docs/api/websocket and HA core source (`homeassistant/components/config/{entity_registry,device_registry,area_registry}.py`, `homeassistant/helpers/{entity_registry,device_registry,area_registry}.py`, `homeassistant/components/websocket_api/commands.py`). Implementers: do NOT guess shapes — use exactly these.

**Auth flow** (server speaks first; auth messages have NO `id` field):

```json
S→C  {"type": "auth_required", "ha_version": "2026.7.0"}
C→S  {"type": "auth", "access_token": "<long-lived token>"}
S→C  {"type": "auth_ok", "ha_version": "2026.7.0"}         // success
S→C  {"type": "auth_invalid", "message": "Invalid access token"}  // failure, server closes
```

**Command/result envelope** (every post-auth client command carries a client-chosen, strictly-increasing integer `id`; the result echoes it):

```json
C→S  {"id": 19, "type": "get_states"}
S→C  {"id": 19, "type": "result", "success": true, "result": [ ...state objects... ]}
S→C  {"id": 12, "type": "result", "success": false,
      "error": {"code": "invalid_format", "message": "..."}}
```

**subscribe_events** (one subscription per event type; subsequent events arrive tagged with the *subscription's* command id):

```json
C→S  {"id": 18, "type": "subscribe_events", "event_type": "state_changed"}
S→C  {"id": 18, "type": "result", "success": true, "result": null}
S→C  {"id": 18, "type": "event", "event": {
        "event_type": "state_changed",
        "data": {
          "entity_id": "light.bed_light",
          "old_state": {"entity_id": "light.bed_light", "state": "off", "attributes": {...}},
          "new_state": {"entity_id": "light.bed_light", "state": "on", "attributes": {...}}
        }}}
```

`old_state` is `null` for newly-appearing entities; `new_state` is `null` when an entity is removed. Attribute-only updates arrive with `old_state.state == new_state.state`.

**Registry-updated event data** (subscribe with `event_type` = `entity_registry_updated` / `device_registry_updated` / `area_registry_updated`):

- `entity_registry_updated`: `{"action": "create"|"remove"|"update", "entity_id": "...", ...}` (`update` adds `changes`, optionally `old_entity_id`)
- `device_registry_updated`: `{"action": "create"|"remove"|"update", "device_id": "..."}` (`update` adds `changes`)
- `area_registry_updated`: `{"action": "create"|"remove"|"update"|"reorder", "area_id": "..."}`

We treat ALL of these identically: refetch all three registries, rebuild the index.

**get_states** → `result` is a list of state objects: `{"entity_id", "state", "attributes": {...}, "last_changed", "last_updated", "context"}`. NOTE: `device_class` lives in `attributes.device_class` — the entity-registry *list* payload does NOT include it (verified: `RegistryEntry.as_partial_dict` keys).

**get_services** → `result` is `{domain: {service: {"name"?: str, "description"?: str, "fields": {field_name: {"name"?, "description"?, "example"?, "required"?, "selector"?: {...}}}, "target"?: {...} | null, "response"?: {...}}}}` (shape matches `HassServices` in home-assistant-js-websocket).

**call_service**:

```json
C→S  {"id": 24, "type": "call_service", "domain": "light", "service": "turn_on",
      "service_data": {"brightness_pct": 40}, "target": {"entity_id": ["light.kitchen"]}}
S→C  {"id": 24, "type": "result", "success": true, "result": {"context": {"id": "..."}}}
```

`service_data` and `target` are optional; `target.entity_id` accepts a list.

**Registry list commands** (registered in HA's `config` component):

```json
C→S  {"id": 5, "type": "config/entity_registry/list"}
S→C  {"id": 5, "type": "result", "success": true, "result": [
       {"entity_id": "light.living_room_lamp", "area_id": "living_room", "device_id": null,
        "name": null, "original_name": "Living Room Lamp", "platform": "hue",
        "disabled_by": null, "hidden_by": null, "entity_category": null, "has_entity_name": true,
        "icon": null, "id": "...", "labels": [], "options": {}, "translation_key": null,
        "unique_id": "...", "config_entry_id": null, "created_at": 0.0, "modified_at": 0.0,
        "categories": {}, "config_subentry_id": null}, ...]}
```

(full key list per `RegistryEntry.as_partial_dict`; we consume `entity_id`, `area_id`, `device_id`, `name`, `original_name`, `disabled_by` and ignore the rest)

```json
C→S  {"id": 6, "type": "config/device_registry/list"}
     → result items: {"id", "area_id", "name", "name_by_user", "manufacturer", "model", ...}
C→S  {"id": 7, "type": "config/area_registry/list"}
     → result items: {"area_id", "name", "aliases", "floor_id", "icon", "labels", "picture",
                      "temperature_entity_id", "humidity_entity_id", "created_at", "modified_at"}
```

---

## File structure

```
home-service/
├── pyproject.toml                 # MODIFY: deps (websockets, aiomqtt, loguru, pyyaml, alfred-sdk), ruff/mypy config
├── Containerfile                  # MODIFY: COPY config/, drop alfred_ext/features
├── README.md                      # MODIFY: rewrite for new architecture
├── config/
│   ├── risk_map.yaml              # CREATE: contract C9 risk mapping (data, not code)
│   └── reflex_tools.yaml          # CREATE: compact reflex-tier tool selection (data, not code)
├── app/
│   ├── server.py                  # REWRITE: create_app(), /mcp, /credentials, /health, wiring
│   ├── ha_connection.py           # CREATE: HAConnection (WebSocket, contract C12)
│   ├── entity_index.py            # CREATE: EntityIndex + EntityInfo
│   ├── risk_map.py                # CREATE: RiskMap + load_reflex_config (supports C9)
│   ├── capability_generator.py    # CREATE: CapabilityGenerator + GeneratedToolSpec (contract C9)
│   ├── home_feature.py            # CREATE: HomeCapabilitiesFeature (generated BaseFeature)
│   ├── state_forwarder.py         # CREATE: StateForwarder → MQTT (contract C11)
│   └── ha_client.py               # TRIM: keep get_states() REST fallback only
├── alfred_ext/
│   ├── register.py                # REWRITE: build_client()/build_credentials_schema() (contract C1)
│   ├── ha_utils.py                # DELETE (to_entity_id name-guessing dies here)
│   └── features/                  # DELETE entire package (lighting.py, scenes.py, __init__.py)
├── docs/qa-backlog/
│   └── ha-live-discovery-smoke.md # CREATE: manual live-apartment QA item
└── tests/
    ├── conftest.py                # CREATE: fake_ha fixture, default_states_map, built_index
    ├── fake_ha.py                 # CREATE: FakeHAServer + default registry/state/service data + eventually()
    ├── test_fake_ha.py            # CREATE
    ├── test_ha_connection.py      # CREATE
    ├── test_entity_index.py       # CREATE
    ├── test_risk_map.py           # CREATE
    ├── test_capability_generator.py # CREATE
    ├── test_home_feature.py       # CREATE
    ├── test_state_forwarder.py    # CREATE
    ├── test_server.py             # REWRITE
    └── test_ha_client.py          # TRIM
```

Key interface flow: `HAConnection` (Task 3) → `EntityIndex` (Task 4) → `RiskMap` (Task 5) → `CapabilityGenerator` (Task 6) → `HomeCapabilitiesFeature` (Task 7); `StateForwarder` (Task 8) hangs off `HAConnection` listeners; `app/server.py` (Task 9) is the composition root.

---

### Task 1: Toolchain, dependencies, and Plan 1 SDK gate

**Files:**
- Modify: `pyproject.toml`
- (no source changes yet)

**Interfaces:**
- Consumes: alfred-sdk from `/Users/anirudhlath/code/private/alfred/alfred/sdk` (Plan 1 must be merged).
- Produces: a working 3.13 venv with `websockets`, `aiomqtt`, `loguru`, `pyyaml`, ruff/mypy config that every later task relies on.

- [ ] **Step 1: Replace `pyproject.toml` with the new dependency set and tool config**

```toml
[project]
name = "home-service"
version = "0.2.0"
description = "Home Assistant wrapper microservice — discovery, generated capabilities, state ingest"
requires-python = ">=3.13"
dependencies = [
    "httpx>=0.27",
    "fastapi>=0.115",
    "uvicorn>=0.34",
    "python-dotenv>=1.0",
    "websockets>=14.0",
    "aiomqtt>=2.0",
    "loguru>=0.7",
    "pyyaml>=6.0",
    "pydantic>=2.0",
    "alfred-sdk>=0.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.15",
    "mypy>=2.1",
    "types-PyYAML>=6.0",
]

[tool.setuptools.packages.find]
include = ["app*", "alfred_ext*"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"

[tool.ruff]
line-length = 100
target-version = "py313"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.mypy]
strict = true
python_version = "3.13"
```

Notes: `alfred-sdk` moves from the old `[project.optional-dependencies].alfred` extra into required dependencies — `app/` now imports the SDK directly (it is the sanctioned bridge, Pillar 2). It is not on PyPI, so every install command co-installs it from source path (already-installed distributions satisfy the requirement, which is also why the Containerfile's two-step install keeps working).

- [ ] **Step 2: Create the venv and install**

Run:
```bash
cd /Users/anirudhlath/code/private/alfred/home-service
uv venv --python 3.13
uv pip install -e ".[dev]" /Users/anirudhlath/code/private/alfred/alfred/sdk
```
Expected: install resolves cleanly; `alfred-sdk` installed from the local path.

- [ ] **Step 3: Verify the Plan 1 SDK contract (HARD GATE)**

Run:
```bash
.venv/bin/python -c "
import dataclasses, inspect
from alfred_sdk import AlfredClient
from alfred_sdk.feature import CredentialField, CredentialSchema, ToolManifest, ToolMeta
sig = inspect.signature(AlfredClient.__init__)
assert 'credentials_schema' in sig.parameters, 'AlfredClient missing credentials_schema'
assert 'credentials_endpoint' in sig.parameters, 'AlfredClient missing credentials_endpoint'
meta_fields = {f.name for f in dataclasses.fields(ToolMeta)}
assert {'audience', 'risk'} <= meta_fields, f'ToolMeta missing audience/risk: {meta_fields}'
assert {'audience', 'risk'} <= set(ToolManifest.model_fields), 'ToolManifest missing audience/risk'
assert {'label', 'field_type', 'required'} <= set(CredentialField.model_fields)
print('Plan 1 SDK contract OK')
"
```
Expected: `Plan 1 SDK contract OK`.
If ANY assertion fails: **STOP. Plan 1 (SDK/bus/core credential flow) is not merged. Do not improvise the SDK changes here.**

- [ ] **Step 4: Confirm existing tests still pass**

Run: `uv run pytest -q`
Expected: `8 passed` (6 in `tests/test_server.py`, 2 in `tests/test_ha_client.py`).

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add pyproject.toml
git commit -m "chore: add websockets/aiomqtt/loguru/pyyaml deps, ruff+mypy config, require alfred-sdk

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Fake Home Assistant WebSocket server fixture

**Files:**
- Create: `tests/fake_ha.py`
- Create: `tests/conftest.py`
- Test: `tests/test_fake_ha.py`

**Interfaces:**
- Consumes: `websockets.asyncio.server.serve` (test-side server from the same library the client uses).
- Produces (used by Tasks 3, 4, 6, 7, 9):
  - `FakeHAServer(token="test-token", states=None, services=None, entity_registry=None, device_registry=None, area_registry=None)` with: `await start()`, `await stop()`, `url: str` property (`http://127.0.0.1:{port}`), `token: str`, `states`/`services`/`entity_registry`/`device_registry`/`area_registry` mutable attrs, `service_calls: list[dict[str, Any]]`, `subscriptions: dict[str, int]`, `auth_attempts: int`, `fail_service_calls: bool`, `await push_state_changed(entity_id, old_state, new_state, attributes=None)`, `await push_registry_updated(kind, data)` (kind ∈ `entity|device|area`), `await drop_connections()`.
  - Module constants `DEFAULT_AREA_REGISTRY`, `DEFAULT_DEVICE_REGISTRY`, `DEFAULT_ENTITY_REGISTRY`, `DEFAULT_STATES`, `DEFAULT_SERVICES`.
  - `async def eventually(predicate, *, timeout=2.0, interval=0.02) -> None` test helper.
  - conftest fixtures: `fake_ha` (started server), `default_states_map` (`dict[str, HAEntityState]` — wired in Task 3 once `HAEntityState` exists; created here returning raw dicts is NOT done — see Step 4 note).

The fake speaks the exact verified message shapes from the "Verified HA WebSocket API message shapes" section above.

- [ ] **Step 1: Write `tests/fake_ha.py`**

```python
"""Fake Home Assistant WebSocket server for tests.

Speaks the exact message shapes of the HA WebSocket API (verified against
https://developers.home-assistant.io/docs/api/websocket and HA core source):
auth_required/auth/auth_ok/auth_invalid, subscribe_events, get_states,
get_services, config/{entity,device,area}_registry/list, call_service, and
event push for state_changed / *_registry_updated.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve

DEFAULT_AREA_REGISTRY: list[dict[str, Any]] = [
    {"area_id": "living_room", "name": "Living Room", "aliases": [], "floor_id": None,
     "icon": None, "labels": [], "picture": None},
    {"area_id": "bedroom", "name": "Bedroom", "aliases": [], "floor_id": None,
     "icon": None, "labels": [], "picture": None},
    {"area_id": "garage", "name": "Garage", "aliases": [], "floor_id": None,
     "icon": None, "labels": [], "picture": None},
]

DEFAULT_DEVICE_REGISTRY: list[dict[str, Any]] = [
    {"id": "dev-tv", "area_id": "living_room", "name": "Living Room TV",
     "name_by_user": None, "manufacturer": "LG", "model": "C3"},
    {"id": "dev-garage-opener", "area_id": "garage", "name": "Garage Door Opener",
     "name_by_user": None, "manufacturer": "Chamberlain", "model": "MyQ"},
]

DEFAULT_ENTITY_REGISTRY: list[dict[str, Any]] = [
    {"entity_id": "light.living_room_lamp", "area_id": "living_room", "device_id": None,
     "name": None, "original_name": "Living Room Lamp", "disabled_by": None, "platform": "hue"},
    {"entity_id": "light.bedroom_lamp", "area_id": "bedroom", "device_id": None,
     "name": "Bedroom Lamp", "original_name": None, "disabled_by": None, "platform": "hue"},
    {"entity_id": "light.closet", "area_id": None, "device_id": None,
     "name": None, "original_name": "Closet Light", "disabled_by": None, "platform": "hue"},
    {"entity_id": "light.disabled_lamp", "area_id": None, "device_id": None,
     "name": None, "original_name": "Disabled Lamp", "disabled_by": "user", "platform": "hue"},
    {"entity_id": "switch.coffee_maker", "area_id": "living_room", "device_id": None,
     "name": None, "original_name": "Coffee Maker", "disabled_by": None, "platform": "tplink"},
    {"entity_id": "media_player.tv", "area_id": None, "device_id": "dev-tv",
     "name": None, "original_name": "TV", "disabled_by": None, "platform": "webostv"},
    {"entity_id": "scene.movie_night", "area_id": None, "device_id": None,
     "name": None, "original_name": "Movie Night", "disabled_by": None, "platform": "homeassistant"},
    {"entity_id": "climate.thermostat", "area_id": "living_room", "device_id": None,
     "name": None, "original_name": "Thermostat", "disabled_by": None, "platform": "nest"},
    {"entity_id": "lock.front_door", "area_id": None, "device_id": None,
     "name": None, "original_name": "Front Door", "disabled_by": None, "platform": "august"},
    {"entity_id": "cover.garage_door", "area_id": None, "device_id": "dev-garage-opener",
     "name": None, "original_name": "Garage Door", "disabled_by": None, "platform": "myq"},
    {"entity_id": "binary_sensor.hallway_motion", "area_id": None, "device_id": None,
     "name": None, "original_name": "Hallway Motion", "disabled_by": None, "platform": "zha"},
]

DEFAULT_STATES: list[dict[str, Any]] = [
    {"entity_id": "light.living_room_lamp", "state": "off",
     "attributes": {"friendly_name": "Living Room Lamp"}},
    {"entity_id": "light.bedroom_lamp", "state": "on",
     "attributes": {"friendly_name": "Bedroom Lamp", "brightness": 128}},
    {"entity_id": "switch.coffee_maker", "state": "off",
     "attributes": {"friendly_name": "Coffee Maker"}},
    {"entity_id": "media_player.tv", "state": "idle",
     "attributes": {"friendly_name": "TV"}},
    {"entity_id": "scene.movie_night", "state": "scening",
     "attributes": {"friendly_name": "Movie Night"}},
    {"entity_id": "climate.thermostat", "state": "heat",
     "attributes": {"friendly_name": "Thermostat", "current_temperature": 21.5}},
    {"entity_id": "lock.front_door", "state": "locked",
     "attributes": {"friendly_name": "Front Door"}},
    {"entity_id": "cover.garage_door", "state": "closed",
     "attributes": {"friendly_name": "Garage Door", "device_class": "garage"}},
    {"entity_id": "binary_sensor.hallway_motion", "state": "off",
     "attributes": {"friendly_name": "Hallway Motion", "device_class": "motion"}},
    {"entity_id": "sensor.outdoor_temp", "state": "18.4",
     "attributes": {"friendly_name": "Outdoor Temp", "unit_of_measurement": "°C"}},
]

_TARGET = {"entity": [{}]}

DEFAULT_SERVICES: dict[str, Any] = {
    "light": {
        "turn_on": {
            "name": "Turn on", "description": "Turn on one or more lights.",
            "fields": {
                "brightness_pct": {"name": "Brightness", "description": "Brightness percentage.",
                                   "example": 50, "selector": {"number": {"min": 0, "max": 100}}},
                "color_name": {"name": "Color", "description": "Color name.",
                               "selector": {"text": None}},
            },
            "target": _TARGET,
        },
        "turn_off": {"name": "Turn off", "description": "Turn off one or more lights.",
                     "fields": {}, "target": _TARGET},
        "toggle": {"name": "Toggle", "description": "Toggle one or more lights.",
                   "fields": {}, "target": _TARGET},
    },
    "switch": {
        "turn_on": {"name": "Turn on", "description": "Turn a switch on.",
                    "fields": {}, "target": _TARGET},
        "turn_off": {"name": "Turn off", "description": "Turn a switch off.",
                     "fields": {}, "target": _TARGET},
        "toggle": {"name": "Toggle", "description": "Toggle a switch.",
                   "fields": {}, "target": _TARGET},
    },
    "media_player": {
        "turn_on": {"name": "Turn on", "description": "Turn a media player on.",
                    "fields": {}, "target": _TARGET},
        "turn_off": {"name": "Turn off", "description": "Turn a media player off.",
                     "fields": {}, "target": _TARGET},
        "media_play": {"name": "Play", "description": "Start playing media.",
                       "fields": {}, "target": _TARGET},
        "media_pause": {"name": "Pause", "description": "Pause playing media.",
                        "fields": {}, "target": _TARGET},
        "volume_set": {
            "name": "Set volume", "description": "Set the playback volume.",
            "fields": {"volume_level": {"name": "Level", "description": "Volume 0..1.",
                                        "required": True,
                                        "selector": {"number": {"min": 0, "max": 1}}}},
            "target": _TARGET,
        },
    },
    "scene": {
        "turn_on": {"name": "Activate", "description": "Activate a scene.",
                    "fields": {}, "target": _TARGET},
    },
    "climate": {
        "set_temperature": {
            "name": "Set target temperature",
            "description": "Set the target temperature.",
            "fields": {"temperature": {"name": "Temperature",
                                       "description": "Target temperature.",
                                       "selector": {"number": {"min": 7, "max": 35}}}},
            "target": _TARGET,
        },
        "set_hvac_mode": {
            "name": "Set HVAC mode", "description": "Set the HVAC operation mode.",
            "fields": {"hvac_mode": {"name": "Mode", "description": "heat, cool, off, ...",
                                     "selector": {"select": {"options": ["heat", "cool", "off"]}}}},
            "target": _TARGET,
        },
    },
    "lock": {
        "lock": {"name": "Lock", "description": "Lock a lock.", "fields": {}, "target": _TARGET},
        "unlock": {"name": "Unlock", "description": "Unlock a lock.",
                   "fields": {}, "target": _TARGET},
    },
    "cover": {
        "open_cover": {"name": "Open", "description": "Open a cover.",
                       "fields": {}, "target": _TARGET},
        "close_cover": {"name": "Close", "description": "Close a cover.",
                        "fields": {}, "target": _TARGET},
    },
    # A domain with services but NO entities in the fixture — must generate no tools.
    "homeassistant": {
        "restart": {"name": "Restart", "description": "Restart Home Assistant.", "fields": {}},
    },
}


async def eventually(
    predicate: Callable[[], bool], *, timeout: float = 2.0, interval: float = 0.02
) -> None:
    """Await until predicate() is true or fail the test (test-only helper)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(interval)
    raise AssertionError("condition not met within timeout")


class FakeHAServer:
    """Minimal in-process Home Assistant WebSocket API double."""

    def __init__(
        self,
        *,
        token: str = "test-token",
        states: list[dict[str, Any]] | None = None,
        services: dict[str, Any] | None = None,
        entity_registry: list[dict[str, Any]] | None = None,
        device_registry: list[dict[str, Any]] | None = None,
        area_registry: list[dict[str, Any]] | None = None,
    ) -> None:
        self.token = token
        self.states = list(states if states is not None else DEFAULT_STATES)
        self.services = dict(services if services is not None else DEFAULT_SERVICES)
        self.entity_registry = list(
            entity_registry if entity_registry is not None else DEFAULT_ENTITY_REGISTRY
        )
        self.device_registry = list(
            device_registry if device_registry is not None else DEFAULT_DEVICE_REGISTRY
        )
        self.area_registry = list(
            area_registry if area_registry is not None else DEFAULT_AREA_REGISTRY
        )
        self.service_calls: list[dict[str, Any]] = []
        self.subscriptions: dict[str, int] = {}
        self.auth_attempts = 0
        self.fail_service_calls = False
        self.port = 0
        self._server: Server | None = None
        self._connections: set[ServerConnection] = set()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def drop_connections(self) -> None:
        """Close all live connections (simulates HA restart). Clears subscriptions."""
        self.subscriptions = {}
        for ws in list(self._connections):
            await ws.close()

    async def _handler(self, ws: ServerConnection) -> None:
        self._connections.add(ws)
        try:
            await ws.send(json.dumps({"type": "auth_required", "ha_version": "2026.7.0"}))
            msg = json.loads(await ws.recv())
            self.auth_attempts += 1
            if msg.get("type") != "auth" or msg.get("access_token") != self.token:
                await ws.send(
                    json.dumps({"type": "auth_invalid", "message": "Invalid access token"})
                )
                return
            await ws.send(json.dumps({"type": "auth_ok", "ha_version": "2026.7.0"}))
            async for raw in ws:
                await self._handle_command(ws, json.loads(raw))
        except Exception:
            pass  # connection torn down mid-test — fine
        finally:
            self._connections.discard(ws)

    async def _handle_command(self, ws: ServerConnection, msg: dict[str, Any]) -> None:
        msg_id = int(msg["id"])
        match msg.get("type"):
            case "subscribe_events":
                self.subscriptions[str(msg.get("event_type", "*"))] = msg_id
                await self._send_result(ws, msg_id, None)
            case "get_states":
                await self._send_result(ws, msg_id, self.states)
            case "get_services":
                await self._send_result(ws, msg_id, self.services)
            case "config/entity_registry/list":
                await self._send_result(ws, msg_id, self.entity_registry)
            case "config/device_registry/list":
                await self._send_result(ws, msg_id, self.device_registry)
            case "config/area_registry/list":
                await self._send_result(ws, msg_id, self.area_registry)
            case "call_service":
                if self.fail_service_calls:
                    await ws.send(json.dumps({
                        "id": msg_id, "type": "result", "success": False,
                        "error": {"code": "service_validation_error", "message": "boom"},
                    }))
                    return
                self.service_calls.append({
                    "domain": msg.get("domain"), "service": msg.get("service"),
                    "service_data": msg.get("service_data"), "target": msg.get("target"),
                })
                await self._send_result(ws, msg_id, {"context": {"id": "ctx-1"}})
            case other:
                await ws.send(json.dumps({
                    "id": msg_id, "type": "result", "success": False,
                    "error": {"code": "unknown_command", "message": f"Unknown command: {other}"},
                }))

    async def _send_result(self, ws: ServerConnection, msg_id: int, result: Any) -> None:
        await ws.send(json.dumps({"id": msg_id, "type": "result", "success": True,
                                  "result": result}))

    async def push_state_changed(
        self,
        entity_id: str,
        old_state: str | None,
        new_state: str | None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        sub_id = self.subscriptions["state_changed"]
        attrs = attributes or {}
        data: dict[str, Any] = {
            "entity_id": entity_id,
            "old_state": (
                {"entity_id": entity_id, "state": old_state, "attributes": attrs}
                if old_state is not None else None
            ),
            "new_state": (
                {"entity_id": entity_id, "state": new_state, "attributes": attrs}
                if new_state is not None else None
            ),
        }
        await self._broadcast({
            "id": sub_id, "type": "event",
            "event": {"event_type": "state_changed", "data": data},
        })

    async def push_registry_updated(self, kind: str, data: dict[str, Any]) -> None:
        """kind: 'entity' | 'device' | 'area'."""
        event_type = f"{kind}_registry_updated"
        sub_id = self.subscriptions[event_type]
        await self._broadcast({
            "id": sub_id, "type": "event",
            "event": {"event_type": event_type, "data": data},
        })

    async def _broadcast(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message)
        for ws in list(self._connections):
            await ws.send(payload)
```

- [ ] **Step 2: Write `tests/conftest.py` (fake_ha fixture only for now)**

```python
"""Shared fixtures for home-service tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.fake_ha import FakeHAServer


@pytest.fixture
async def fake_ha() -> AsyncIterator[FakeHAServer]:
    server = FakeHAServer()
    await server.start()
    yield server
    await server.stop()
```

- [ ] **Step 3: Write the fixture self-tests `tests/test_fake_ha.py`**

```python
"""Self-tests for the fake HA WebSocket server."""

from __future__ import annotations

import json

from websockets.asyncio.client import connect

from tests.fake_ha import FakeHAServer


def _ws_url(server: FakeHAServer) -> str:
    return f"ws://127.0.0.1:{server.port}/api/websocket"


async def test_auth_ok_flow_and_get_states(fake_ha: FakeHAServer) -> None:
    async with connect(_ws_url(fake_ha)) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": "test-token"}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "get_states"}))
        msg = json.loads(await ws.recv())
        assert msg == {"id": 1, "type": "result", "success": True, "result": fake_ha.states}


async def test_auth_invalid_on_wrong_token(fake_ha: FakeHAServer) -> None:
    async with connect(_ws_url(fake_ha)) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": "nope"}))
        assert json.loads(await ws.recv())["type"] == "auth_invalid"
    assert fake_ha.auth_attempts == 1


async def test_subscribe_and_event_push(fake_ha: FakeHAServer) -> None:
    async with connect(_ws_url(fake_ha)) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": "test-token"}))
        await ws.recv()
        await ws.send(json.dumps({"id": 7, "type": "subscribe_events",
                                  "event_type": "state_changed"}))
        assert json.loads(await ws.recv())["success"] is True
        await fake_ha.push_state_changed("light.bedroom_lamp", "on", "off",
                                         {"friendly_name": "Bedroom Lamp"})
        event = json.loads(await ws.recv())
        assert event["id"] == 7
        assert event["type"] == "event"
        assert event["event"]["event_type"] == "state_changed"
        assert event["event"]["data"]["entity_id"] == "light.bedroom_lamp"
        assert event["event"]["data"]["new_state"]["state"] == "off"


async def test_call_service_recorded_and_error_mode(fake_ha: FakeHAServer) -> None:
    async with connect(_ws_url(fake_ha)) as ws:
        await ws.recv()
        await ws.send(json.dumps({"type": "auth", "access_token": "test-token"}))
        await ws.recv()
        await ws.send(json.dumps({
            "id": 2, "type": "call_service", "domain": "light", "service": "turn_on",
            "service_data": {"brightness_pct": 40},
            "target": {"entity_id": ["light.bedroom_lamp"]},
        }))
        result = json.loads(await ws.recv())
        assert result["success"] is True and "context" in result["result"]
        assert fake_ha.service_calls == [{
            "domain": "light", "service": "turn_on",
            "service_data": {"brightness_pct": 40},
            "target": {"entity_id": ["light.bedroom_lamp"]},
        }]
        fake_ha.fail_service_calls = True
        await ws.send(json.dumps({"id": 3, "type": "call_service",
                                  "domain": "light", "service": "turn_on"}))
        err = json.loads(await ws.recv())
        assert err["success"] is False
        assert err["error"]["code"] == "service_validation_error"
```

- [ ] **Step 4: Run the fixture self-tests**

Run: `uv run pytest tests/test_fake_ha.py -v`
Expected: `4 passed`.

Note: `default_states_map` and `built_index` fixtures are added to this conftest in Tasks 3 and 4 (they need `HAEntityState`/`EntityIndex` which don't exist yet).

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add tests/fake_ha.py tests/conftest.py tests/test_fake_ha.py
git commit -m "test: fake Home Assistant WebSocket server fixture

Speaks verified HA WS API shapes: auth flow, subscribe_events, get_states,
get_services, config registries, call_service, event push.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: HAConnection — persistent HA WebSocket

**Files:**
- Create: `app/ha_connection.py`
- Modify: `tests/conftest.py` (add `default_states_map` fixture)
- Test: `tests/test_ha_connection.py`

**Interfaces:**
- Consumes: `FakeHAServer`, `eventually` from `tests/fake_ha.py` (Task 2).
- Produces (used by Tasks 4, 7, 8, 9):
  - `ConnState = Literal["connected", "auth_failed", "unreachable", "disconnected"]`
  - `StateListener = Callable[[str, str | None, str | None, dict[str, Any]], Awaitable[None]]` — args `(entity_id, old_state, new_state, attributes)`; `new_state is None` means the entity was removed.
  - `VoidListener = Callable[[], Awaitable[None]]`
  - `class HAEntityState(BaseModel)`: `entity_id: str`, `state: str`, `attributes: dict[str, Any]`
  - `class HAAuthError(Exception)`, `class HACommandError(Exception)` (attrs `.code: str`, `.message: str`)
  - `class HAConnection`:
    - `__init__(self, *, initial_backoff: float = 1.0, max_backoff: float = 60.0)`
    - attrs: `conn_state: ConnState` (starts `"disconnected"`), `states: dict[str, HAEntityState]`, `services_catalog: dict[str, Any]`, `entity_registry: list[dict[str, Any]]`, `device_registry: list[dict[str, Any]]`, `area_registry: list[dict[str, Any]]`
    - `add_state_listener(cb: StateListener) -> None`, `add_registry_listener(cb: VoidListener) -> None`, `add_connect_listener(cb: VoidListener) -> None`
    - `async apply_credentials(url: str, token: str) -> ConnState` — (re)connects; returns state after the first attempt completes
    - `async call_service(domain: str, service: str, service_data: dict[str, Any] | None = None, entity_ids: list[str] | None = None) -> dict[str, Any]`
    - `last_event_age_s() -> float | None`
    - `async stop() -> None`

Behavior contract (C12): auth handshake → subscribe `state_changed` + the three `*_registry_updated` events → fetch registries, `get_states`, `get_services` → mark `connected` → notify connect listeners. Reconnect with exponential backoff on drop (resubscribe + refresh + re-notify). `auth_invalid` sets `auth_failed` and STOPS retrying until new credentials arrive (a bad token never fixes itself). Listeners run inside the reader loop — they MUST NOT issue WebSocket commands (registry refresh is therefore spawned as a separate task).

- [ ] **Step 1: Write the failing tests `tests/test_ha_connection.py`**

```python
"""Tests for HAConnection against the fake HA WebSocket server."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from app.ha_connection import HACommandError, HAConnection
from tests.fake_ha import FakeHAServer, eventually


@pytest.fixture
async def conn() -> AsyncIterator[HAConnection]:
    connection = HAConnection(initial_backoff=0.05, max_backoff=0.2)
    yield connection
    await connection.stop()


async def test_apply_credentials_connects_and_fetches(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    state = await conn.apply_credentials(fake_ha.url, fake_ha.token)
    assert state == "connected"
    assert conn.conn_state == "connected"
    assert conn.states["light.bedroom_lamp"].state == "on"
    assert conn.states["light.bedroom_lamp"].attributes["brightness"] == 128
    assert "light" in conn.services_catalog
    assert len(conn.area_registry) == 3
    assert len(conn.entity_registry) == 11
    assert {
        "state_changed", "entity_registry_updated",
        "device_registry_updated", "area_registry_updated",
    } <= set(fake_ha.subscriptions)


async def test_starts_disconnected_and_no_event_age() -> None:
    connection = HAConnection()
    assert connection.conn_state == "disconnected"
    assert connection.last_event_age_s() is None


async def test_bad_token_sets_auth_failed_and_stops_retrying(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    state = await conn.apply_credentials(fake_ha.url, "wrong-token")
    assert state == "auth_failed"
    await asyncio.sleep(0.3)  # several backoff periods — must NOT retry a bad token
    assert fake_ha.auth_attempts == 1


async def test_unreachable_host_sets_unreachable(conn: HAConnection) -> None:
    state = await conn.apply_credentials("http://127.0.0.1:1", "token")
    assert state == "unreachable"


async def test_call_service_round_trip(fake_ha: FakeHAServer, conn: HAConnection) -> None:
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    result = await conn.call_service(
        "light", "turn_on", {"brightness_pct": 40}, ["light.bedroom_lamp"]
    )
    assert "context" in result
    assert fake_ha.service_calls == [{
        "domain": "light", "service": "turn_on",
        "service_data": {"brightness_pct": 40},
        "target": {"entity_id": ["light.bedroom_lamp"]},
    }]


async def test_call_service_error_raises(fake_ha: FakeHAServer, conn: HAConnection) -> None:
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    fake_ha.fail_service_calls = True
    with pytest.raises(HACommandError) as exc_info:
        await conn.call_service("light", "turn_on", None, ["light.bedroom_lamp"])
    assert exc_info.value.code == "service_validation_error"


async def test_call_service_while_disconnected_raises() -> None:
    connection = HAConnection()
    with pytest.raises(HACommandError) as exc_info:
        await connection.call_service("light", "turn_on")
    assert exc_info.value.code == "not_connected"


async def test_state_changed_updates_states_and_notifies(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    received: list[tuple[str, str | None, str | None, dict[str, Any]]] = []

    async def listener(
        entity_id: str, old: str | None, new: str | None, attrs: dict[str, Any]
    ) -> None:
        received.append((entity_id, old, new, attrs))

    conn.add_state_listener(listener)
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    await fake_ha.push_state_changed(
        "light.bedroom_lamp", "on", "off", {"friendly_name": "Bedroom Lamp"}
    )
    await eventually(lambda: conn.states["light.bedroom_lamp"].state == "off")
    assert received == [
        ("light.bedroom_lamp", "on", "off", {"friendly_name": "Bedroom Lamp"})
    ]
    assert conn.last_event_age_s() is not None


async def test_entity_removal_drops_state(fake_ha: FakeHAServer, conn: HAConnection) -> None:
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    await fake_ha.push_state_changed("light.bedroom_lamp", "on", None)
    await eventually(lambda: "light.bedroom_lamp" not in conn.states)


async def test_registry_update_refetches_and_notifies(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    notified = asyncio.Event()

    async def registry_listener() -> None:
        notified.set()

    conn.add_registry_listener(registry_listener)
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    fake_ha.area_registry = fake_ha.area_registry + [
        {"area_id": "office", "name": "Office", "aliases": [], "floor_id": None,
         "icon": None, "labels": [], "picture": None}
    ]
    await fake_ha.push_registry_updated("area", {"action": "create", "area_id": "office"})
    await eventually(lambda: len(conn.area_registry) == 4)
    await eventually(notified.is_set)


async def test_reconnect_after_drop_resubscribes(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    connects = 0

    async def on_connect() -> None:
        nonlocal connects
        connects += 1

    conn.add_connect_listener(on_connect)
    await conn.apply_credentials(fake_ha.url, fake_ha.token)
    assert connects == 1
    await fake_ha.drop_connections()  # clears fake_ha.subscriptions
    await eventually(lambda: connects == 2, timeout=3.0)
    assert conn.conn_state == "connected"
    assert "state_changed" in fake_ha.subscriptions  # re-subscribed after reconnect


async def test_apply_credentials_switches_servers(
    fake_ha: FakeHAServer, conn: HAConnection
) -> None:
    other = FakeHAServer(
        token="other-token",
        states=[{"entity_id": "light.other", "state": "on",
                 "attributes": {"friendly_name": "Other"}}],
    )
    await other.start()
    try:
        await conn.apply_credentials(fake_ha.url, fake_ha.token)
        assert "light.bedroom_lamp" in conn.states
        state = await conn.apply_credentials(other.url, "other-token")
        assert state == "connected"
        assert "light.other" in conn.states
        assert "light.bedroom_lamp" not in conn.states
    finally:
        await other.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ha_connection.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.ha_connection'`.

- [ ] **Step 3: Write `app/ha_connection.py`**

```python
"""Persistent WebSocket connection to Home Assistant (contract C12).

Message shapes verified against https://developers.home-assistant.io/docs/api/websocket
and HA core source — see the implementation plan's "Verified HA WebSocket API
message shapes" section for the exact envelopes.

Design notes:
- One client-chosen increasing integer id per command; results are correlated
  back to awaiting futures (`_pending`).
- Listeners are awaited inside the reader loop, so they MUST NOT issue
  WebSocket commands (would deadlock the correlation loop). Registry refresh
  therefore runs as a separate coalesced task.
- `auth_invalid` is terminal: no retry until `apply_credentials` is called
  again with new credentials.
- The `websockets` library handles ping/pong keepalive automatically.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field
from websockets.asyncio.client import ClientConnection, connect

ConnState = Literal["connected", "auth_failed", "unreachable", "disconnected"]

StateListener = Callable[[str, str | None, str | None, dict[str, Any]], Awaitable[None]]
VoidListener = Callable[[], Awaitable[None]]

COMMAND_TIMEOUT = 30.0

SUBSCRIBED_EVENTS = (
    "state_changed",
    "entity_registry_updated",
    "device_registry_updated",
    "area_registry_updated",
)


class HAAuthError(Exception):
    """Home Assistant rejected the access token (auth_invalid)."""


class HACommandError(Exception):
    """A WebSocket command failed or could not be sent."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class HAEntityState(BaseModel):
    """One entity's live state (mirror of an /api/states item)."""

    entity_id: str
    state: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class HAConnection:
    """Owns the WebSocket to HA: auth, subscriptions, fetches, service calls."""

    def __init__(self, *, initial_backoff: float = 1.0, max_backoff: float = 60.0) -> None:
        self._initial_backoff = initial_backoff
        self._max_backoff = max_backoff
        self._url = ""
        self._token = ""
        self._ws: ClientConnection | None = None
        self._task: asyncio.Task[None] | None = None
        self._registry_refresh_task: asyncio.Task[None] | None = None
        self._registry_dirty = False
        self._next_msg_id = 1
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._attempt_done = asyncio.Event()

        self.conn_state: ConnState = "disconnected"
        self.states: dict[str, HAEntityState] = {}
        self.services_catalog: dict[str, Any] = {}
        self.entity_registry: list[dict[str, Any]] = []
        self.device_registry: list[dict[str, Any]] = []
        self.area_registry: list[dict[str, Any]] = []

        self._last_event_monotonic: float | None = None
        self._state_listeners: list[StateListener] = []
        self._registry_listeners: list[VoidListener] = []
        self._connect_listeners: list[VoidListener] = []

    # ── listeners ──

    def add_state_listener(self, cb: StateListener) -> None:
        self._state_listeners.append(cb)

    def add_registry_listener(self, cb: VoidListener) -> None:
        self._registry_listeners.append(cb)

    def add_connect_listener(self, cb: VoidListener) -> None:
        self._connect_listeners.append(cb)

    def last_event_age_s(self) -> float | None:
        if self._last_event_monotonic is None:
            return None
        return round(time.monotonic() - self._last_event_monotonic, 1)

    # ── lifecycle ──

    async def apply_credentials(self, url: str, token: str) -> ConnState:
        """Apply (or replace) credentials and (re)connect.

        Returns the connection state after the first attempt completes, so the
        caller (POST /credentials) can immediately report connected-or-failed.
        """
        await self.stop()
        self._url = url.rstrip("/")
        self._token = token
        self.conn_state = "disconnected"
        self._attempt_done = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="ha-connection")
        await self._attempt_done.wait()
        return self.conn_state

    async def stop(self) -> None:
        for task in (self._task, self._registry_refresh_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        self._task = None
        self._registry_refresh_task = None
        self._ws = None

    def _ws_url(self) -> str:
        if self._url.startswith("https://"):
            return "wss://" + self._url.removeprefix("https://") + "/api/websocket"
        return "ws://" + self._url.removeprefix("http://") + "/api/websocket"

    # ── connection loop ──

    async def _run(self) -> None:
        backoff = self._initial_backoff
        while True:
            try:
                async with connect(self._ws_url(), max_size=None) as ws:
                    self._ws = ws
                    await self._handshake(ws)
                    backoff = self._initial_backoff
                    setup = asyncio.create_task(self._on_connected(ws))
                    try:
                        async for raw in ws:
                            await self._handle_message(json.loads(raw))
                    finally:
                        setup.cancel()
                        self._ws = None
                        self._fail_pending()
                self.conn_state = "unreachable"
                logger.warning("HA WebSocket closed — reconnecting in {:.1f}s", backoff)
            except HAAuthError as exc:
                self.conn_state = "auth_failed"
                logger.error("HA rejected token ({}) — waiting for new credentials", exc)
                self._attempt_done.set()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.conn_state = "unreachable"
                logger.warning(
                    "HA connection failed ({}: {}) — retrying in {:.1f}s",
                    type(exc).__name__, exc, backoff,
                )
            self._attempt_done.set()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, self._max_backoff)

    async def _handshake(self, ws: ClientConnection) -> None:
        first = json.loads(await ws.recv())
        if first.get("type") != "auth_required":
            raise HACommandError("protocol", f"expected auth_required, got {first.get('type')}")
        await ws.send(json.dumps({"type": "auth", "access_token": self._token}))
        resp = json.loads(await ws.recv())
        if resp.get("type") == "auth_invalid":
            raise HAAuthError(str(resp.get("message", "invalid token")))
        if resp.get("type") != "auth_ok":
            raise HACommandError("protocol", f"expected auth_ok, got {resp.get('type')}")

    async def _on_connected(self, ws: ClientConnection) -> None:
        """Post-auth setup: subscribe, refresh registries/states/catalog, notify."""
        try:
            for event_type in SUBSCRIBED_EVENTS:
                await self._cmd(ws, {"type": "subscribe_events", "event_type": event_type})
            await self._refresh_registries(ws)
            raw_states: list[dict[str, Any]] = await self._cmd(ws, {"type": "get_states"}) or []
            self.states = {
                s["entity_id"]: HAEntityState(
                    entity_id=s["entity_id"],
                    state=s.get("state", "unknown"),
                    attributes=s.get("attributes", {}),
                )
                for s in raw_states
            }
            self.services_catalog = await self._cmd(ws, {"type": "get_services"}) or {}
            self.conn_state = "connected"
            logger.info(
                "Connected to HA: {} entities, {} areas, {} service domains",
                len(self.states), len(self.area_registry), len(self.services_catalog),
            )
            for cb in self._connect_listeners:
                try:
                    await cb()
                except Exception:
                    logger.exception("connect listener failed")
            self._attempt_done.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("HA post-connect setup failed: {}", exc)
            await ws.close()  # reader loop exits → _run marks unreachable + retries

    async def _refresh_registries(self, ws: ClientConnection) -> None:
        self.entity_registry = await self._cmd(ws, {"type": "config/entity_registry/list"}) or []
        self.device_registry = await self._cmd(ws, {"type": "config/device_registry/list"}) or []
        self.area_registry = await self._cmd(ws, {"type": "config/area_registry/list"}) or []

    # ── command correlation ──

    async def _cmd(self, ws: ClientConnection, payload: dict[str, Any]) -> Any:
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await ws.send(json.dumps({"id": msg_id, **payload}))
            return await asyncio.wait_for(fut, timeout=COMMAND_TIMEOUT)
        finally:
            self._pending.pop(msg_id, None)

    def _fail_pending(self) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(HACommandError("connection_lost", "WebSocket closed"))
        self._pending.clear()

    # ── inbound messages ──

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        match msg.get("type"):
            case "result":
                fut = self._pending.get(int(msg["id"]))
                if fut is not None and not fut.done():
                    if msg.get("success"):
                        fut.set_result(msg.get("result"))
                    else:
                        err = msg.get("error") or {}
                        fut.set_exception(HACommandError(
                            str(err.get("code", "unknown")), str(err.get("message", ""))
                        ))
            case "event":
                event = msg.get("event") or {}
                event_type = str(event.get("event_type", ""))
                if event_type == "state_changed":
                    await self._handle_state_changed(event.get("data") or {})
                elif event_type.endswith("_registry_updated"):
                    self._schedule_registry_refresh()
            case _:
                pass

    async def _handle_state_changed(self, data: dict[str, Any]) -> None:
        entity_id = str(data.get("entity_id", ""))
        if not entity_id:
            return
        new = data.get("new_state")
        old = data.get("old_state")
        new_state = str(new["state"]) if new else None
        old_state = str(old["state"]) if old else None
        attributes: dict[str, Any] = dict(new.get("attributes") or {}) if new else {}
        if new is None:
            self.states.pop(entity_id, None)
        else:
            self.states[entity_id] = HAEntityState(
                entity_id=entity_id, state=new_state or "unknown", attributes=attributes
            )
        self._last_event_monotonic = time.monotonic()
        for cb in self._state_listeners:
            try:
                await cb(entity_id, old_state, new_state, attributes)
            except Exception:
                logger.exception("state listener failed for {}", entity_id)

    # ── registry refresh (coalesced task — never run inside the reader loop) ──

    def _schedule_registry_refresh(self) -> None:
        self._registry_dirty = True
        if self._registry_refresh_task is not None and not self._registry_refresh_task.done():
            return
        self._registry_refresh_task = asyncio.create_task(
            self._registry_refresh_loop(), name="ha-registry-refresh"
        )

    async def _registry_refresh_loop(self) -> None:
        while self._registry_dirty:
            self._registry_dirty = False
            ws = self._ws
            if ws is None:
                return  # reconnect path refreshes registries anyway
            try:
                await self._refresh_registries(ws)
            except Exception as exc:
                logger.warning("Registry refresh failed: {}", exc)
                return
            for cb in self._registry_listeners:
                try:
                    await cb()
                except Exception:
                    logger.exception("registry listener failed")

    # ── service calls ──

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        entity_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        ws = self._ws
        if self.conn_state != "connected" or ws is None:
            raise HACommandError(
                "not_connected", f"cannot call {domain}.{service}: HA state is {self.conn_state}"
            )
        payload: dict[str, Any] = {"type": "call_service", "domain": domain, "service": service}
        if service_data:
            payload["service_data"] = service_data
        if entity_ids:
            payload["target"] = {"entity_id": entity_ids}
        result = await self._cmd(ws, payload)
        return result if isinstance(result, dict) else {}
```

- [ ] **Step 4: Add the `default_states_map` fixture to `tests/conftest.py`**

Add to the imports: `from app.ha_connection import HAEntityState` and `from tests.fake_ha import DEFAULT_STATES, FakeHAServer` (extend the existing import). Append:

```python
@pytest.fixture
def default_states_map() -> dict[str, HAEntityState]:
    return {
        s["entity_id"]: HAEntityState(
            entity_id=s["entity_id"], state=s["state"], attributes=s.get("attributes", {})
        )
        for s in DEFAULT_STATES
    }
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_ha_connection.py -v`
Expected: `12 passed`. (If `test_reconnect_after_drop_resubscribes` flakes, the backoff constants in the `conn` fixture are wrong — it must use `initial_backoff=0.05`.)

- [ ] **Step 6: Lint/type-check the new module**

Run:
```bash
uv run ruff check --fix app/ha_connection.py tests/test_ha_connection.py
uv run ruff format app/ha_connection.py tests/test_ha_connection.py
uv run mypy --strict app/ha_connection.py
```
Expected: no ruff findings; `Success: no issues found in 1 source file`.

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/ha_connection.py tests/test_ha_connection.py tests/conftest.py
git commit -m "feat: HAConnection — persistent HA WebSocket with reconnect and fetches

Auth handshake, message-id correlation, subscribe state_changed +
registry-updated events, registry/state/service-catalog fetches,
call_service over the socket, exponential-backoff reconnect,
runtime apply_credentials. auth_invalid is terminal until new credentials.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: EntityIndex — registry-driven metadata and name resolution

**Files:**
- Create: `app/entity_index.py`
- Modify: `tests/conftest.py` (add `built_index` fixture)
- Test: `tests/test_entity_index.py`

**Interfaces:**
- Consumes: `HAEntityState` (Task 3); registry dict shapes from the verified-shapes section.
- Produces (used by Tasks 5–7, 9):
  - `class EntityInfo(BaseModel)`: `entity_id: str`, `friendly_name: str`, `domain: str`, `device_class: str | None`, `area: str | None` (area NAME, not id), `device: str | None` (device display name)
  - `class EntityIndex`:
    - `rebuild(*, entity_registry: list[dict[str, Any]], device_registry: list[dict[str, Any]], area_registry: list[dict[str, Any]], states: dict[str, HAEntityState]) -> None`
    - `get(entity_id: str) -> EntityInfo | None`
    - `entity_count() -> int`, `area_count() -> int`, `area_names() -> list[str]`
    - `domains() -> set[str]`, `entities_for_domain(domain: str) -> list[EntityInfo]` (sorted by entity_id)
    - `resolve(domain: str, target: str) -> list[str]` — target may be an entity_id, an area name, or a friendly name (case-insensitive); raises `LookupError` listing available options.

Notes locked in: the index is the UNION of live states and enabled registry entries (states-only entities like `sensor.outdoor_temp` and registry-only entities like `light.closet` both appear); registry entries with `disabled_by` set are excluded; `device_class` comes from state `attributes.device_class` (the registry list payload does not carry it — verified); area resolution falls back through `device_id` → device's `area_id`. This replaces `to_entity_id()` name-guessing (deleted in Task 9).

- [ ] **Step 1: Write the failing tests `tests/test_entity_index.py`**

```python
"""Tests for EntityIndex build + resolution."""

from __future__ import annotations

import pytest

from app.entity_index import EntityIndex
from app.ha_connection import HAEntityState
from tests.fake_ha import (
    DEFAULT_AREA_REGISTRY,
    DEFAULT_DEVICE_REGISTRY,
    DEFAULT_ENTITY_REGISTRY,
)


def test_counts_and_disabled_exclusion(built_index: EntityIndex) -> None:
    # 10 enabled registry entries ∪ 10 states = 11 unique
    # (light.closet is registry-only, sensor.outdoor_temp is states-only)
    assert built_index.entity_count() == 11
    assert built_index.area_count() == 3
    assert built_index.get("light.disabled_lamp") is None
    assert built_index.get("sensor.outdoor_temp") is not None
    assert built_index.get("light.closet") is not None


def test_domains_and_area_names(built_index: EntityIndex) -> None:
    assert built_index.domains() == {
        "light", "switch", "media_player", "scene", "climate",
        "lock", "cover", "binary_sensor", "sensor",
    }
    assert built_index.area_names() == ["Bedroom", "Garage", "Living Room"]


def test_friendly_name_precedence(built_index: EntityIndex) -> None:
    # state attribute wins
    assert built_index.get("light.bedroom_lamp").friendly_name == "Bedroom Lamp"  # type: ignore[union-attr]
    # registry original_name for a registry-only entity with no state
    assert built_index.get("light.closet").friendly_name == "Closet Light"  # type: ignore[union-attr]
    # entity_id fallback for a states-only entity without friendly_name
    index = EntityIndex()
    index.rebuild(
        entity_registry=[], device_registry=[], area_registry=[],
        states={"sensor.raw_thing": HAEntityState(
            entity_id="sensor.raw_thing", state="1", attributes={})},
    )
    assert index.get("sensor.raw_thing").friendly_name == "raw thing"  # type: ignore[union-attr]


def test_area_via_device_and_device_name(built_index: EntityIndex) -> None:
    tv = built_index.get("media_player.tv")
    assert tv is not None
    assert tv.area == "Living Room"  # from device dev-tv
    assert tv.device == "Living Room TV"
    garage = built_index.get("cover.garage_door")
    assert garage is not None
    assert garage.area == "Garage"
    assert garage.device_class == "garage"  # from state attributes


def test_resolve_by_area_name(built_index: EntityIndex) -> None:
    assert built_index.resolve("light", "Living Room") == ["light.living_room_lamp"]
    assert built_index.resolve("light", "living room") == ["light.living_room_lamp"]


def test_resolve_by_friendly_name_and_entity_id(built_index: EntityIndex) -> None:
    assert built_index.resolve("light", "Bedroom Lamp") == ["light.bedroom_lamp"]
    assert built_index.resolve("scene", "Movie Night") == ["scene.movie_night"]
    assert built_index.resolve("light", "light.closet") == ["light.closet"]


def test_resolve_unknown_raises_with_options(built_index: EntityIndex) -> None:
    with pytest.raises(LookupError) as exc_info:
        built_index.resolve("light", "attic")
    message = str(exc_info.value)
    assert "attic" in message
    assert "Bedroom" in message  # available areas listed
    assert "Closet Light" in message  # available entities listed


def test_resolve_rejects_wrong_domain_entity_id(built_index: EntityIndex) -> None:
    with pytest.raises(LookupError):
        built_index.resolve("light", "switch.coffee_maker")


def test_rebuild_replaces_previous_data(built_index: EntityIndex) -> None:
    renamed_areas = [
        {**a, "name": "Lounge"} if a["area_id"] == "living_room" else a
        for a in DEFAULT_AREA_REGISTRY
    ]
    built_index.rebuild(
        entity_registry=DEFAULT_ENTITY_REGISTRY,
        device_registry=DEFAULT_DEVICE_REGISTRY,
        area_registry=renamed_areas,
        states={},
    )
    assert built_index.resolve("light", "Lounge") == ["light.living_room_lamp"]
    with pytest.raises(LookupError):
        built_index.resolve("light", "Living Room")
```

- [ ] **Step 2: Add the `built_index` fixture to `tests/conftest.py`**

Extend imports with `from app.entity_index import EntityIndex` and `from tests.fake_ha import (DEFAULT_AREA_REGISTRY, DEFAULT_DEVICE_REGISTRY, DEFAULT_ENTITY_REGISTRY, DEFAULT_STATES, FakeHAServer)`. Append:

```python
@pytest.fixture
def built_index(default_states_map: dict[str, HAEntityState]) -> EntityIndex:
    index = EntityIndex()
    index.rebuild(
        entity_registry=DEFAULT_ENTITY_REGISTRY,
        device_registry=DEFAULT_DEVICE_REGISTRY,
        area_registry=DEFAULT_AREA_REGISTRY,
        states=default_states_map,
    )
    return index
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_entity_index.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.entity_index'`.

- [ ] **Step 4: Write `app/entity_index.py`**

```python
"""EntityIndex — discovered HA entity metadata + human-name resolution.

Built from the HA entity/device/area registries plus live states; rebuilt on
registry-updated events. Replaces the deleted to_entity_id() name-guessing:
all tool execution resolves areas / friendly names → real entity IDs here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.ha_connection import HAEntityState


class EntityInfo(BaseModel):
    """Resolved metadata for one entity."""

    entity_id: str
    friendly_name: str
    domain: str
    device_class: str | None = None
    area: str | None = None
    device: str | None = None


class EntityIndex:
    """entity_id → EntityInfo, with area / friendly-name → entity_ids resolution."""

    def __init__(self) -> None:
        self._entities: dict[str, EntityInfo] = {}
        self._area_names: list[str] = []

    def rebuild(
        self,
        *,
        entity_registry: list[dict[str, Any]],
        device_registry: list[dict[str, Any]],
        area_registry: list[dict[str, Any]],
        states: dict[str, HAEntityState],
    ) -> None:
        areas = {str(a["area_id"]): str(a["name"]) for a in area_registry}
        devices = {str(d["id"]): d for d in device_registry}
        reg_by_id = {str(e["entity_id"]): e for e in entity_registry}

        entities: dict[str, EntityInfo] = {}
        for entity_id in sorted(set(states) | set(reg_by_id)):
            reg = reg_by_id.get(entity_id, {})
            if reg.get("disabled_by"):
                continue
            st = states.get(entity_id)
            attributes: dict[str, Any] = st.attributes if st is not None else {}
            device = devices.get(str(reg.get("device_id") or ""), {})
            area_id = reg.get("area_id") or device.get("area_id")
            friendly = (
                attributes.get("friendly_name")
                or reg.get("name")
                or reg.get("original_name")
                or entity_id.split(".", 1)[-1].replace("_", " ")
            )
            entities[entity_id] = EntityInfo(
                entity_id=entity_id,
                friendly_name=str(friendly),
                domain=entity_id.split(".", 1)[0],
                device_class=attributes.get("device_class"),
                area=areas.get(str(area_id)) if area_id else None,
                device=(device.get("name_by_user") or device.get("name")) if device else None,
            )
        self._entities = entities
        self._area_names = sorted(areas.values())

    def get(self, entity_id: str) -> EntityInfo | None:
        return self._entities.get(entity_id)

    def entity_count(self) -> int:
        return len(self._entities)

    def area_count(self) -> int:
        return len(self._area_names)

    def area_names(self) -> list[str]:
        return list(self._area_names)

    def domains(self) -> set[str]:
        return {e.domain for e in self._entities.values()}

    def entities_for_domain(self, domain: str) -> list[EntityInfo]:
        return sorted(
            (e for e in self._entities.values() if e.domain == domain),
            key=lambda e: e.entity_id,
        )

    def resolve(self, domain: str, target: str) -> list[str]:
        """Resolve an area name / friendly name / entity_id to concrete entity_ids.

        Raises LookupError listing the available options so the calling LLM
        can self-correct from the in-band /mcp error.
        """
        wanted = target.strip().casefold()
        candidates = self.entities_for_domain(domain)
        exact = [e.entity_id for e in candidates if e.entity_id.casefold() == wanted]
        if exact:
            return exact
        by_area = [
            e.entity_id for e in candidates
            if e.area is not None and e.area.casefold() == wanted
        ]
        if by_area:
            return by_area
        by_name = [e.entity_id for e in candidates if e.friendly_name.casefold() == wanted]
        if by_name:
            return by_name
        available_areas = sorted({e.area for e in candidates if e.area is not None})
        available_names = sorted(e.friendly_name for e in candidates)
        raise LookupError(
            f"No {domain} entity matches '{target}'. "
            f"Areas: {', '.join(available_areas) or 'none'}. "
            f"{domain} entities: {', '.join(available_names) or 'none'}."
        )
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_entity_index.py -v`
Expected: `9 passed`.

- [ ] **Step 6: Lint/type-check**

Run:
```bash
uv run ruff check --fix app/entity_index.py tests/test_entity_index.py tests/conftest.py
uv run ruff format app/entity_index.py tests/test_entity_index.py tests/conftest.py
uv run mypy --strict app/entity_index.py
```
Expected: clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/entity_index.py tests/test_entity_index.py tests/conftest.py
git commit -m "feat: EntityIndex — registry-driven entity metadata and name resolution

entity_id → {friendly_name, domain, device_class, area, device}; resolves
area / friendly name / entity_id per domain; excludes disabled entities;
area falls back through the device registry.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Risk map and reflex tool config (data, not code)

**Files:**
- Create: `config/risk_map.yaml`
- Create: `config/reflex_tools.yaml`
- Create: `app/risk_map.py`
- Test: `tests/test_risk_map.py`

**Interfaces:**
- Consumes: `EntityIndex` (Task 4).
- Produces (used by Tasks 6, 9):
  - `Risk = Literal["benign", "elevated", "critical"]`
  - `class RiskMap` with `@classmethod load(path: Path) -> RiskMap`, `risk_for(domain: str, device_class: str | None) -> Risk`, `domain_tool_risk(domain: str, index: EntityIndex) -> Risk`
  - `def load_reflex_config(path: Path) -> dict[str, dict[str, list[str]]]` — domain → service → extra optional field names for the compact reflex tier.

Contract C9 rules implemented here: default risk `benign`; `lock`/`alarm_control_panel` critical; cover critical iff `device_class` ∈ {garage, garage_door, gate, door} else elevated; `script`/`automation`/`climate`/`water_heater`/`cover`/`vacuum` elevated. `domain_tool_risk` takes the MAX risk across a domain's discovered entities (a garage-door cover makes ALL cover tools critical — safety-first v1; per-device_class tool splitting is a later refinement).

- [ ] **Step 1: Write `config/risk_map.yaml` (contract C9, verbatim)**

```yaml
# Risk tiers for generated tools (contract C9). Data, not code — edit freely.
critical:
  domains: [lock, alarm_control_panel]
  cover_device_classes: [garage, garage_door, gate, door]
elevated:
  domains: [script, automation, climate, water_heater, cover, vacuum]
```

- [ ] **Step 2: Write `config/reflex_tools.yaml`**

```yaml
# Compact reflex-tier tool selection: domain → service → extra optional fields.
# Only these services become audience:"reflex" tools (kept small so the SLM
# prompt stays small). Everything else is reachable for the Conscious engine
# via generated per-domain tools or the home.call_service escape hatch.
light:
  turn_on: [brightness_pct]
  turn_off: []
switch:
  turn_on: []
  turn_off: []
media_player:
  turn_on: []
  turn_off: []
  media_play: []
  media_pause: []
  volume_set: [volume_level]
scene:
  turn_on: []
```

- [ ] **Step 3: Write the failing tests `tests/test_risk_map.py`**

```python
"""Tests for the data-driven risk map and reflex tool config."""

from __future__ import annotations

from pathlib import Path

from app.entity_index import EntityIndex
from app.ha_connection import HAEntityState
from app.risk_map import RiskMap, load_reflex_config

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def test_risk_for_matches_contract_c9() -> None:
    risk_map = RiskMap.load(CONFIG_DIR / "risk_map.yaml")
    assert risk_map.risk_for("lock", None) == "critical"
    assert risk_map.risk_for("alarm_control_panel", None) == "critical"
    assert risk_map.risk_for("cover", "garage") == "critical"
    assert risk_map.risk_for("cover", "garage_door") == "critical"
    assert risk_map.risk_for("cover", "awning") == "elevated"
    assert risk_map.risk_for("cover", None) == "elevated"
    assert risk_map.risk_for("climate", None) == "elevated"
    assert risk_map.risk_for("script", None) == "elevated"
    assert risk_map.risk_for("vacuum", None) == "elevated"
    assert risk_map.risk_for("light", None) == "benign"
    assert risk_map.risk_for("media_player", None) == "benign"


def _index_with_covers(device_classes: list[str | None]) -> EntityIndex:
    states = {
        f"cover.c{i}": HAEntityState(
            entity_id=f"cover.c{i}", state="closed",
            attributes={"device_class": dc} if dc else {},
        )
        for i, dc in enumerate(device_classes)
    }
    index = EntityIndex()
    index.rebuild(entity_registry=[], device_registry=[], area_registry=[], states=states)
    return index


def test_domain_tool_risk_takes_max_over_entities() -> None:
    risk_map = RiskMap.load(CONFIG_DIR / "risk_map.yaml")
    assert risk_map.domain_tool_risk("cover", _index_with_covers(["awning"])) == "elevated"
    assert risk_map.domain_tool_risk("cover", _index_with_covers(["awning", "garage"])) == "critical"
    # no entities → base domain risk
    assert risk_map.domain_tool_risk("cover", _index_with_covers([])) == "elevated"
    assert risk_map.domain_tool_risk("light", _index_with_covers([])) == "benign"


def test_load_reflex_config() -> None:
    config = load_reflex_config(CONFIG_DIR / "reflex_tools.yaml")
    assert set(config) == {"light", "switch", "media_player", "scene"}
    assert config["light"]["turn_on"] == ["brightness_pct"]
    assert config["light"]["turn_off"] == []
    assert config["media_player"]["volume_set"] == ["volume_level"]
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/test_risk_map.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.risk_map'`.

- [ ] **Step 5: Write `app/risk_map.py`**

```python
"""Risk mapping and reflex-tier tool config — data, not code (contract C9)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml

from app.entity_index import EntityIndex

Risk = Literal["benign", "elevated", "critical"]

_RISK_ORDER: dict[Risk, int] = {"benign": 0, "elevated": 1, "critical": 2}


class RiskMap:
    """Domain/device_class → risk tier, loaded from config/risk_map.yaml."""

    def __init__(
        self,
        critical_domains: set[str],
        critical_cover_device_classes: set[str],
        elevated_domains: set[str],
    ) -> None:
        self._critical_domains = critical_domains
        self._critical_cover_device_classes = critical_cover_device_classes
        self._elevated_domains = elevated_domains

    @classmethod
    def load(cls, path: Path) -> RiskMap:
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        critical = raw.get("critical") or {}
        elevated = raw.get("elevated") or {}
        return cls(
            critical_domains={str(d) for d in critical.get("domains") or []},
            critical_cover_device_classes={
                str(d) for d in critical.get("cover_device_classes") or []
            },
            elevated_domains={str(d) for d in elevated.get("domains") or []},
        )

    def risk_for(self, domain: str, device_class: str | None) -> Risk:
        if domain in self._critical_domains:
            return "critical"
        if domain == "cover" and device_class in self._critical_cover_device_classes:
            return "critical"
        if domain in self._elevated_domains:
            return "elevated"
        return "benign"

    def domain_tool_risk(self, domain: str, index: EntityIndex) -> Risk:
        """Max risk across the domain's discovered entities.

        A garage-door cover makes ALL cover tools critical (safety-first v1 —
        relaxing later is easy, the reverse is not).
        """
        risks: list[Risk] = [
            self.risk_for(domain, e.device_class) for e in index.entities_for_domain(domain)
        ]
        if not risks:
            risks = [self.risk_for(domain, None)]
        return max(risks, key=lambda r: _RISK_ORDER[r])


def load_reflex_config(path: Path) -> dict[str, dict[str, list[str]]]:
    """domain → service → extra optional field names for the compact reflex tier."""
    raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
    return {
        str(domain): {
            str(service): [str(f) for f in fields or []]
            for service, fields in (services or {}).items()
        }
        for domain, services in raw.items()
    }
```

- [ ] **Step 6: Run the tests, lint, type-check**

Run:
```bash
uv run pytest tests/test_risk_map.py -v
uv run ruff check --fix app/risk_map.py tests/test_risk_map.py
uv run ruff format app/risk_map.py tests/test_risk_map.py
uv run mypy --strict app/risk_map.py
```
Expected: `3 passed`; ruff/mypy clean.

- [ ] **Step 7: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add config/risk_map.yaml config/reflex_tools.yaml app/risk_map.py tests/test_risk_map.py
git commit -m "feat: data-driven risk map and reflex tool config (contract C9)

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: CapabilityGenerator — generated tool surface

**Files:**
- Create: `app/capability_generator.py`
- Test: `tests/test_capability_generator.py`

**Interfaces:**
- Consumes: `EntityIndex` (Task 4); `Risk`, `RiskMap`, `load_reflex_config` (Task 5); `ToolMeta`, `ToolParameter` from `alfred_sdk.feature` (with Plan 1's `audience`/`risk` fields on `ToolMeta`).
- Produces (used by Tasks 7, 9):
  - `Audience = Literal["reflex", "conscious"]`
  - `REFLEX_DOMAINS = frozenset({"light", "switch", "media_player", "scene"})` (contract C9 audience rule)
  - `MAX_LISTED_ENTITIES = 30`
  - `@dataclass(frozen=True) class FieldSpec`: `name: str`, `type: str`, `description: str`
  - `@dataclass(frozen=True) class GeneratedToolSpec`: `tool_name: str` (e.g. `"home.light_turn_on"` — what Alfred dispatches to `/mcp`), `method_name: str` (e.g. `"light_turn_on"` — the bound attribute name on the feature), `domain: str | None` (None → generic call_service escape hatch), `service: str | None`, `description: str`, `audience: Audience`, `risk: Risk`, `fields: tuple[FieldSpec, ...]`, `targeted: bool`
  - `class CapabilityGenerator`:
    - `__init__(risk_map: RiskMap, reflex_config: dict[str, dict[str, list[str]]])`
    - `generate(catalog: dict[str, Any], index: EntityIndex) -> list[GeneratedToolSpec]` — the STATIC tool set (frozen for process lifetime; dispatch bindings are created once)
    - `build_tool_meta(spec: GeneratedToolSpec, index: EntityIndex) -> ToolMeta` — LIVE values (current area/entity names) injected into parameter descriptions at manifest-build time

Design decisions locked in:
- **Tool naming:** `home.{domain}_{service}` + `home.call_service`. The last dot-segment must be a unique valid attribute name because `AlfredClient.discover_features_from_classes` binds dispatch via `getattr(instance, tool_meta.name.split(".")[-1])` (see alfred `sdk/alfred_sdk/client.py:66-75`). `{domain}_{service}` is collision-free by construction.
- **Reflex tier** (audience `"reflex"`, risk `"benign"` per C9): only domain/service pairs listed in `config/reflex_tools.yaml` that ALSO exist in this HA's catalog AND have entities. Parameters: `target` + only the yaml-listed extra fields (compactness — e.g. `brightness_pct` yes, `color_name` no).
- **Conscious tier:** every service of every remaining domain that has entities, with ALL catalog fields; `targeted` iff the catalog entry has a non-null `target`. Risk = `RiskMap.domain_tool_risk`.
- **Escape hatch:** `home.call_service` (audience `"conscious"`, risk `"critical"` — it can reach locks/alarms, so v1 requires confirmation; per-call risk inspection is a later refinement).
- **Domains with services but no entities** (e.g. `homeassistant`) and **domains with entities but no services** (e.g. `sensor`) generate nothing.
- **Live-values injection:** the `target` parameter description embeds current area names and entity friendly names — this is the existing "available entity values" pattern the Reflex Engine renders into its prompt (alfred `core/reflex/engine.py:90-94` prints each parameter description).
- **Staleness rule:** the spec SET is frozen after first connect (new HA *domains* need a service restart — documented in Task 10 README); descriptions/context stay live because `build_tool_meta` reads the index at every manifest build.

- [ ] **Step 1: Write the failing tests `tests/test_capability_generator.py`**

```python
"""Tests for CapabilityGenerator output: tool set, audience/risk tagging, shapes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.capability_generator import CapabilityGenerator, GeneratedToolSpec
from app.entity_index import EntityIndex
from app.ha_connection import HAEntityState
from app.risk_map import RiskMap, load_reflex_config
from tests.fake_ha import DEFAULT_SERVICES

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


@pytest.fixture
def generator() -> CapabilityGenerator:
    return CapabilityGenerator(
        RiskMap.load(CONFIG_DIR / "risk_map.yaml"),
        load_reflex_config(CONFIG_DIR / "reflex_tools.yaml"),
    )


@pytest.fixture
def specs(generator: CapabilityGenerator, built_index: EntityIndex) -> list[GeneratedToolSpec]:
    return generator.generate(DEFAULT_SERVICES, built_index)


def _by_name(specs: list[GeneratedToolSpec]) -> dict[str, GeneratedToolSpec]:
    return {s.tool_name: s for s in specs}


def test_generated_tool_set_is_exactly_expected(specs: list[GeneratedToolSpec]) -> None:
    assert {s.tool_name for s in specs} == {
        # reflex tier (from config/reflex_tools.yaml ∩ catalog ∩ entities)
        "home.light_turn_on", "home.light_turn_off",
        "home.switch_turn_on", "home.switch_turn_off",
        "home.media_player_turn_on", "home.media_player_turn_off",
        "home.media_player_media_play", "home.media_player_media_pause",
        "home.media_player_volume_set",
        "home.scene_turn_on",
        # conscious tier (remaining domains with entities)
        "home.climate_set_temperature", "home.climate_set_hvac_mode",
        "home.lock_lock", "home.lock_unlock",
        "home.cover_open_cover", "home.cover_close_cover",
        # escape hatch
        "home.call_service",
    }
    # light.toggle exists in the catalog but is not in reflex_tools.yaml → absent
    # homeassistant.restart has no entities → absent


def test_audience_and_risk_tagging(specs: list[GeneratedToolSpec]) -> None:
    by_name = _by_name(specs)
    for name in ("home.light_turn_on", "home.switch_turn_off",
                 "home.media_player_volume_set", "home.scene_turn_on"):
        assert by_name[name].audience == "reflex"
        assert by_name[name].risk == "benign"
    assert by_name["home.climate_set_temperature"].audience == "conscious"
    assert by_name["home.climate_set_temperature"].risk == "elevated"
    assert by_name["home.lock_unlock"].risk == "critical"
    # garage-door cover in the fixture elevates ALL cover tools to critical
    assert by_name["home.cover_close_cover"].risk == "critical"
    assert by_name["home.call_service"].audience == "conscious"
    assert by_name["home.call_service"].risk == "critical"


def test_reflex_tool_fields_are_compact(specs: list[GeneratedToolSpec]) -> None:
    light_on = _by_name(specs)["home.light_turn_on"]
    assert [f.name for f in light_on.fields] == ["brightness_pct"]  # color_name excluded
    assert light_on.targeted is True
    assert light_on.method_name == "light_turn_on"
    assert light_on.description == "Turn on one or more lights."


def test_conscious_tool_fields_from_catalog(specs: list[GeneratedToolSpec]) -> None:
    set_temp = _by_name(specs)["home.climate_set_temperature"]
    assert [f.name for f in set_temp.fields] == ["temperature"]
    field = set_temp.fields[0]
    assert field.type == "float"  # number selector
    assert "Target temperature." in field.description


def test_build_tool_meta_injects_live_values(
    generator: CapabilityGenerator,
    specs: list[GeneratedToolSpec],
    built_index: EntityIndex,
) -> None:
    light_on = _by_name(specs)["home.light_turn_on"]
    meta = generator.build_tool_meta(light_on, built_index)
    assert meta.name == "home.light_turn_on"
    assert meta.audience == "reflex"
    assert meta.risk == "benign"
    target_desc = meta.parameters["target"].description
    assert "Available areas: Bedroom, Living Room." in target_desc
    assert "Bedroom Lamp" in target_desc and "Closet Light" in target_desc
    assert meta.parameters["brightness_pct"].type == "float"
    assert "Example: 50." in meta.parameters["brightness_pct"].description


def test_call_service_meta_shape(
    generator: CapabilityGenerator,
    specs: list[GeneratedToolSpec],
    built_index: EntityIndex,
) -> None:
    escape = _by_name(specs)["home.call_service"]
    meta = generator.build_tool_meta(escape, built_index)
    assert set(meta.parameters) == {"domain", "service", "entity_id", "data"}
    assert meta.parameters["data"].type == "dict"


def test_untargeted_service_has_no_target_param(generator: CapabilityGenerator) -> None:
    catalog: dict[str, Any] = {
        "vacuum": {"start": {"name": "Start", "description": "Start cleaning.",
                             "fields": {}}}  # no "target" key
    }
    index = EntityIndex()
    index.rebuild(
        entity_registry=[], device_registry=[], area_registry=[],
        states={"vacuum.robo": HAEntityState(entity_id="vacuum.robo", state="docked",
                                             attributes={})},
    )
    specs = generator.generate(catalog, index)
    by_name = _by_name(specs)
    assert by_name["home.vacuum_start"].targeted is False
    meta = generator.build_tool_meta(by_name["home.vacuum_start"], index)
    assert "target" not in meta.parameters
    assert by_name["home.vacuum_start"].risk == "elevated"  # vacuum is elevated in risk map
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capability_generator.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.capability_generator'`.

- [ ] **Step 3: Write `app/capability_generator.py`**

```python
"""CapabilityGenerator — generates the tool surface from HA's own registries.

Crosses the HA service catalog (get_services) with the EntityIndex and emits
SDK ToolMeta tagged with audience ("reflex" | "conscious") and risk
("benign" | "elevated" | "critical") per config/risk_map.yaml (contract C9).

Live area/entity values are injected into parameter descriptions at manifest
build time — the Reflex Engine renders parameter descriptions into its prompt
(alfred core/reflex/engine.py "Include parameter descriptions").
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from alfred_sdk.feature import ToolMeta, ToolParameter

from app.entity_index import EntityIndex
from app.risk_map import Risk, RiskMap

Audience = Literal["reflex", "conscious"]

# Contract C9 audience rule: tools over these domains → audience reflex, risk benign.
REFLEX_DOMAINS = frozenset({"light", "switch", "media_player", "scene"})

MAX_LISTED_ENTITIES = 30  # cap injected entity lists to bound prompt size


@dataclass(frozen=True)
class FieldSpec:
    """A non-target tool parameter derived from an HA service field."""

    name: str
    type: str
    description: str


@dataclass(frozen=True)
class GeneratedToolSpec:
    """Static shape of one generated tool (descriptions get live values later)."""

    tool_name: str  # e.g. "home.light_turn_on" — what Alfred dispatches to /mcp
    method_name: str  # e.g. "light_turn_on" — bound attribute on the feature
    domain: str | None  # None → generic call_service escape hatch
    service: str | None
    description: str
    audience: Audience
    risk: Risk
    fields: tuple[FieldSpec, ...]
    targeted: bool


def _field_type(fdef: dict[str, Any]) -> str:
    selector = fdef.get("selector") or {}
    if "number" in selector:
        return "float"
    if "boolean" in selector:
        return "bool"
    if "object" in selector:
        return "dict"
    return "str"


def _field_spec(name: str, fdef: dict[str, Any] | None) -> FieldSpec:
    fdef = fdef or {}
    description = str(fdef.get("description") or fdef.get("name") or name)
    example = fdef.get("example")
    if example is not None:
        description += f" Example: {example}."
    if fdef.get("required"):
        description += " (required)"
    return FieldSpec(name=name, type=_field_type(fdef), description=description)


def _service_description(svc: dict[str, Any], domain: str, service: str) -> str:
    raw = str(svc.get("description") or svc.get("name") or f"{domain}.{service}")
    return raw.split("\n")[0]


class CapabilityGenerator:
    """HA service catalog × EntityIndex → tagged tool specs and manifests."""

    def __init__(
        self, risk_map: RiskMap, reflex_config: dict[str, dict[str, list[str]]]
    ) -> None:
        self._risk_map = risk_map
        self._reflex_config = reflex_config

    def generate(self, catalog: dict[str, Any], index: EntityIndex) -> list[GeneratedToolSpec]:
        """Build the static tool set (frozen for the process lifetime).

        New HA domains appearing later require a service restart; entity/area
        renames stay live via build_tool_meta() + context snapshots.
        """
        specs: list[GeneratedToolSpec] = []
        domains_present = index.domains()
        for domain in sorted(catalog):
            services: dict[str, Any] = catalog[domain] or {}
            if domain not in domains_present:
                continue  # a service domain with no entities gets no tools
            if domain in REFLEX_DOMAINS:
                specs.extend(self._reflex_specs(domain, services))
            else:
                specs.extend(self._conscious_specs(domain, services, index))
        specs.append(
            GeneratedToolSpec(
                tool_name="home.call_service",
                method_name="call_service",
                domain=None,
                service=None,
                description=(
                    "Call any Home Assistant service directly — escape hatch for "
                    "operations without a dedicated tool."
                ),
                audience="conscious",
                risk="critical",  # can reach locks/alarms; v1 requires confirmation
                fields=(),
                targeted=False,
            )
        )
        return specs

    def _reflex_specs(self, domain: str, services: dict[str, Any]) -> list[GeneratedToolSpec]:
        specs: list[GeneratedToolSpec] = []
        for service, extra_fields in self._reflex_config.get(domain, {}).items():
            svc = services.get(service)
            if svc is None:
                continue  # this HA doesn't offer the service
            catalog_fields: dict[str, Any] = svc.get("fields") or {}
            fields = tuple(
                _field_spec(f, catalog_fields.get(f))
                for f in extra_fields
                if f in catalog_fields
            )
            specs.append(
                GeneratedToolSpec(
                    tool_name=f"home.{domain}_{service}",
                    method_name=f"{domain}_{service}",
                    domain=domain,
                    service=service,
                    description=_service_description(svc, domain, service),
                    audience="reflex",
                    risk="benign",  # contract C9 audience rule
                    fields=fields,
                    targeted=True,
                )
            )
        return specs

    def _conscious_specs(
        self, domain: str, services: dict[str, Any], index: EntityIndex
    ) -> list[GeneratedToolSpec]:
        risk = self._risk_map.domain_tool_risk(domain, index)
        specs: list[GeneratedToolSpec] = []
        for service in sorted(services):
            svc: dict[str, Any] = services[service] or {}
            catalog_fields: dict[str, Any] = svc.get("fields") or {}
            fields = tuple(
                _field_spec(name, fdef) for name, fdef in sorted(catalog_fields.items())
            )
            specs.append(
                GeneratedToolSpec(
                    tool_name=f"home.{domain}_{service}",
                    method_name=f"{domain}_{service}",
                    domain=domain,
                    service=service,
                    description=_service_description(svc, domain, service),
                    audience="conscious",
                    risk=risk,
                    fields=fields,
                    targeted=svc.get("target") is not None,
                )
            )
        return specs

    def build_tool_meta(self, spec: GeneratedToolSpec, index: EntityIndex) -> ToolMeta:
        """Build a ToolMeta with LIVE area/entity values in parameter descriptions."""
        parameters: dict[str, ToolParameter] = {}
        if spec.domain is None:
            parameters = {
                "domain": ToolParameter(type="str", description="HA service domain, e.g. 'light'."),
                "service": ToolParameter(type="str", description="Service name, e.g. 'turn_on'."),
                "entity_id": ToolParameter(
                    type="str",
                    description="Optional entity_id to target, e.g. 'light.living_room_lamp'.",
                ),
                "data": ToolParameter(type="dict", description="Optional service data fields."),
            }
        else:
            if spec.targeted:
                parameters["target"] = ToolParameter(
                    type="str", description=self._target_description(spec.domain, index)
                )
            for f in spec.fields:
                parameters[f.name] = ToolParameter(type=f.type, description=f.description)
        return ToolMeta(
            name=spec.tool_name,
            description=spec.description,
            parameters=parameters,
            audience=spec.audience,
            risk=spec.risk,
        )

    def _target_description(self, domain: str, index: EntityIndex) -> str:
        entities = index.entities_for_domain(domain)
        areas = sorted({e.area for e in entities if e.area is not None})
        names = sorted({e.friendly_name for e in entities})[:MAX_LISTED_ENTITIES]
        description = "Area name, entity friendly name, or entity_id."
        if areas:
            description += f" Available areas: {', '.join(areas)}."
        if names:
            description += f" Available {domain} entities: {', '.join(names)}."
        return description
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_capability_generator.py -v`
Expected: `7 passed`.

- [ ] **Step 5: Lint/type-check**

Run:
```bash
uv run ruff check --fix app/capability_generator.py tests/test_capability_generator.py
uv run ruff format app/capability_generator.py tests/test_capability_generator.py
uv run mypy --strict app/capability_generator.py
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/capability_generator.py tests/test_capability_generator.py
git commit -m "feat: CapabilityGenerator — tool surface generated from HA catalog

Reflex tier from config/reflex_tools.yaml with live area/entity values in
parameter descriptions; conscious tier for remaining domains; call_service
escape hatch; audience/risk tagging per contract C9.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: HomeCapabilitiesFeature — generated tools bound for SDK dispatch

**Files:**
- Create: `app/home_feature.py`
- Test: `tests/test_home_feature.py`

**Interfaces:**
- Consumes: `GeneratedToolSpec`, `CapabilityGenerator` (Task 6); `EntityIndex` (Task 4); `HAEntityState` (Task 3); `BaseFeature`, `ToolMeta` from `alfred_sdk.feature`; `ContextEntry`, `ContextSnapshot` from `alfred_sdk.context`.
- Produces (used by Task 9):
  - `class HAConnectionLike(Protocol)` — structural slice of `HAConnection` the feature needs: attrs `states: dict[str, HAEntityState]`, `services_catalog: dict[str, Any]`; method `async call_service(domain, service, service_data=None, entity_ids=None) -> dict[str, Any]` (lets tests use a stub without a socket).
  - `class HomeCapabilitiesContext`: `__init__(conn: HAConnectionLike, index: EntityIndex, generator: CapabilityGenerator, specs: list[GeneratedToolSpec])`, attrs `conn`/`index`/`generator`/`specs`.
  - `class HomeCapabilitiesFeature(BaseFeature)`: `feature_name = "home"`; `__init__(ctx: HomeCapabilitiesContext)` binds one async handler per spec via `setattr(self, spec.method_name, ...)` so `AlfredClient.discover_features_from_classes` finds them; `get_tools() -> list[ToolMeta]` overrides BaseFeature discovery to return generator-built metas with live values (same override pattern as alfred `core/triggers/feature.py:49` `TriggerFeature.get_tools`); `async get_context() -> ContextSnapshot` built from `conn.states` (live, event-fed).
  - `CONTEXT_ATTR_ALLOWLIST` — attributes kept in context snapshots (prompt-size control).

`/mcp` dispatch path after this task: `HomeAgent` POSTs `{"method": "home.light_turn_on", "params": {"target": "Living Room", "brightness_pct": 50}, "id": ...}` → `AlfredClient.dispatch` → bound handler → `EntityIndex.resolve` → `HAConnection.call_service`. The JSON-RPC contract is byte-compatible with today's.

- [ ] **Step 1: Write the failing tests `tests/test_home_feature.py`**

```python
"""Tests for HomeCapabilitiesFeature: handler binding, dispatch, context."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alfred_sdk import AlfredClient
import pytest

from app.capability_generator import CapabilityGenerator
from app.entity_index import EntityIndex
from app.ha_connection import HAEntityState
from app.home_feature import HomeCapabilitiesContext, HomeCapabilitiesFeature
from app.risk_map import RiskMap, load_reflex_config
from tests.fake_ha import DEFAULT_SERVICES

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class StubHA:
    """Implements HAConnectionLike without a socket."""

    def __init__(
        self, states: dict[str, HAEntityState], services_catalog: dict[str, Any]
    ) -> None:
        self.states = states
        self.services_catalog = services_catalog
        self.calls: list[tuple[str, str, dict[str, Any] | None, list[str] | None]] = []

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        entity_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((domain, service, service_data, entity_ids))
        return {"context": {"id": "stub"}}


@pytest.fixture
def feature_env(
    built_index: EntityIndex, default_states_map: dict[str, HAEntityState]
) -> tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext]:
    generator = CapabilityGenerator(
        RiskMap.load(CONFIG_DIR / "risk_map.yaml"),
        load_reflex_config(CONFIG_DIR / "reflex_tools.yaml"),
    )
    specs = generator.generate(DEFAULT_SERVICES, built_index)
    stub = StubHA(default_states_map, DEFAULT_SERVICES)
    ctx = HomeCapabilitiesContext(conn=stub, index=built_index, generator=generator, specs=specs)
    return HomeCapabilitiesFeature(ctx), stub, ctx


def test_every_tool_has_a_bound_handler(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, _stub, ctx = feature_env
    for spec in ctx.specs:
        handler = getattr(feature, spec.method_name, None)
        assert callable(handler), f"missing handler for {spec.tool_name}"
    # get_tools names match the dispatch-binding convention (last segment = attr)
    for meta in feature.get_tools():
        assert hasattr(feature, meta.name.split(".")[-1])


async def test_dispatch_via_alfred_client_resolves_area(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    _feature, stub, ctx = feature_env
    client = AlfredClient(service_name="home-service", service_endpoint="http://x/mcp")
    client.discover_features_from_classes([HomeCapabilitiesFeature], ctx=ctx)
    result = await client.dispatch(
        "home.light_turn_on", {"target": "Living Room", "brightness_pct": 50}
    )
    assert result["status"] == "ok"
    assert result["entity_ids"] == ["light.living_room_lamp"]
    assert stub.calls == [("light", "turn_on", {"brightness_pct": 50},
                           ["light.living_room_lamp"])]


async def test_dispatch_friendly_name_and_scene(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, stub, _ctx = feature_env
    await feature._execute_by_name("home.scene_turn_on", {"target": "movie night"})
    assert stub.calls[-1] == ("scene", "turn_on", {}, ["scene.movie_night"])


async def test_call_service_escape_hatch(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, stub, _ctx = feature_env
    result = await feature._execute_by_name(
        "home.call_service",
        {"domain": "lock", "service": "unlock", "entity_id": "lock.front_door"},
    )
    assert result["status"] == "ok"
    assert stub.calls == [("lock", "unlock", {}, ["lock.front_door"])]


async def test_missing_target_raises(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, _stub, _ctx = feature_env
    with pytest.raises(ValueError, match="target"):
        await feature._execute_by_name("home.light_turn_on", {"brightness_pct": 10})


async def test_unknown_target_raises_lookup_error(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, _stub, _ctx = feature_env
    with pytest.raises(LookupError, match="Areas:"):
        await feature._execute_by_name("home.light_turn_on", {"target": "attic"})


async def test_get_context_buckets_and_filters_attributes(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, stub, _ctx = feature_env
    stub.states["weather.home"] = HAEntityState(
        entity_id="weather.home", state="sunny",
        attributes={"friendly_name": "Home", "forecast": [{"big": "blob"}]},
    )
    snapshot = await feature.get_context()
    # domains with services → controllable; without → sensors
    assert "light" in snapshot.controllable
    assert "lock" in snapshot.controllable
    assert "sensor" in snapshot.sensors
    assert "binary_sensor" in snapshot.sensors
    assert "weather" in snapshot.sensors  # not in DEFAULT_SERVICES catalog
    lights = {e.entity_id: e for e in snapshot.controllable["light"]}
    assert lights["light.bedroom_lamp"].state == "on"
    assert lights["light.bedroom_lamp"].attributes["brightness"] == 128
    weather = snapshot.sensors["weather"][0]
    assert "forecast" not in weather.attributes  # filtered by allowlist
    assert weather.attributes["friendly_name"] == "Home"


def test_to_manifest_propagates_audience_and_risk(
    feature_env: tuple[HomeCapabilitiesFeature, StubHA, HomeCapabilitiesContext],
) -> None:
    feature, _stub, _ctx = feature_env
    manifest = feature.to_manifest()
    assert manifest.name == "home"
    tools = {t.name: t for t in manifest.tools}
    assert tools["home.light_turn_on"].audience == "reflex"
    assert tools["home.light_turn_on"].risk == "benign"
    assert tools["home.lock_unlock"].audience == "conscious"
    assert tools["home.lock_unlock"].risk == "critical"
```

Note: `_execute_by_name(tool_name, params)` is a small public-ish test seam on the feature (defined in Step 3) that looks up the spec and runs the same `_execute` path the bound handlers use — it keeps tests from reaching through `getattr` string plumbing.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_home_feature.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.home_feature'`.

- [ ] **Step 3: Write `app/home_feature.py`**

```python
"""HomeCapabilitiesFeature — the generated BaseFeature bound for SDK dispatch.

One feature instance carries ALL generated tools. Handlers are bound as
instance attributes named after each spec's method_name because
AlfredClient.discover_features_from_classes resolves dispatch callables via
getattr(instance, tool_meta.name.split(".")[-1]).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from alfred_sdk.context import ContextEntry, ContextSnapshot
from alfred_sdk.feature import BaseFeature, ToolMeta

from app.capability_generator import CapabilityGenerator, GeneratedToolSpec
from app.entity_index import EntityIndex
from app.ha_connection import HAEntityState

# Attributes kept in context snapshots — everything else is dropped to keep
# the Reflex prompt small (HA attributes can be huge, e.g. weather forecasts).
CONTEXT_ATTR_ALLOWLIST = frozenset({
    "friendly_name", "device_class", "brightness", "current_temperature",
    "temperature", "media_title", "battery_level", "unit_of_measurement",
})


class HAConnectionLike(Protocol):
    """Structural slice of HAConnection used by generated capabilities."""

    states: dict[str, HAEntityState]
    services_catalog: dict[str, Any]

    async def call_service(
        self,
        domain: str,
        service: str,
        service_data: dict[str, Any] | None = None,
        entity_ids: list[str] | None = None,
    ) -> dict[str, Any]: ...


class HomeCapabilitiesContext:
    """Dependencies handed to HomeCapabilitiesFeature by AlfredClient discovery."""

    def __init__(
        self,
        conn: HAConnectionLike,
        index: EntityIndex,
        generator: CapabilityGenerator,
        specs: list[GeneratedToolSpec],
    ) -> None:
        self.conn = conn
        self.index = index
        self.generator = generator
        self.specs = specs


class HomeCapabilitiesFeature(BaseFeature):
    """Generated Home Assistant control surface."""

    feature_name = "home"

    def __init__(self, ctx: HomeCapabilitiesContext) -> None:
        super().__init__()
        self._conn = ctx.conn
        self._index = ctx.index
        self._generator = ctx.generator
        self._specs = ctx.specs
        self._specs_by_name = {spec.tool_name: spec for spec in ctx.specs}
        for spec in ctx.specs:
            setattr(self, spec.method_name, self._make_handler(spec))

    def get_tools(self) -> list[ToolMeta]:
        """Override BaseFeature discovery — inject live area/entity values.

        Same override pattern as alfred's TriggerFeature.get_tools(). Called on
        every register(), so re-registration keeps descriptions fresh.
        """
        return [self._generator.build_tool_meta(spec, self._index) for spec in self._specs]

    def _make_handler(
        self, spec: GeneratedToolSpec
    ) -> Callable[..., Awaitable[dict[str, Any]]]:
        async def handler(**params: Any) -> dict[str, Any]:
            return await self._execute(spec, params)

        handler.__name__ = spec.method_name
        return handler

    async def _execute_by_name(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        """Test seam: run a tool by its registered name."""
        return await self._execute(self._specs_by_name[tool_name], params)

    async def _execute(self, spec: GeneratedToolSpec, params: dict[str, Any]) -> dict[str, Any]:
        if spec.domain is None or spec.service is None:
            # home.call_service escape hatch
            domain = str(params["domain"])
            service = str(params["service"])
            data = dict(params.get("data") or {})
            entity_id = params.get("entity_id")
            await self._conn.call_service(
                domain, service, data, [str(entity_id)] if entity_id else None
            )
            return {"domain": domain, "service": service, "entity_id": entity_id, "status": "ok"}
        entity_ids: list[str] | None = None
        if spec.targeted:
            target = params.get("target")
            if not target:
                raise ValueError(f"Missing required parameter 'target' for {spec.tool_name}")
            entity_ids = self._index.resolve(spec.domain, str(target))
        service_data = {
            f.name: params[f.name] for f in spec.fields if params.get(f.name) is not None
        }
        await self._conn.call_service(spec.domain, spec.service, service_data, entity_ids)
        return {
            "domain": spec.domain,
            "service": spec.service,
            "entity_ids": entity_ids,
            "service_data": service_data,
            "status": "ok",
        }

    async def get_context(self) -> ContextSnapshot:
        """Live context from the connection's state store (fed by WS events)."""
        controllable: dict[str, list[ContextEntry]] = {}
        sensors: dict[str, list[ContextEntry]] = {}
        catalog_domains = set(self._conn.services_catalog)
        for entity_id in sorted(self._conn.states):
            st = self._conn.states[entity_id]
            domain = entity_id.split(".", 1)[0]
            attrs = {k: v for k, v in st.attributes.items() if k in CONTEXT_ATTR_ALLOWLIST}
            entry = ContextEntry(entity_id=entity_id, state=st.state, attributes=attrs)
            bucket = controllable if domain in catalog_domains else sensors
            bucket.setdefault(domain, []).append(entry)
        return ContextSnapshot(controllable=controllable, sensors=sensors)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_home_feature.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Lint/type-check**

Run:
```bash
uv run ruff check --fix app/home_feature.py tests/test_home_feature.py
uv run ruff format app/home_feature.py tests/test_home_feature.py
uv run mypy --strict app/home_feature.py
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/home_feature.py tests/test_home_feature.py
git commit -m "feat: HomeCapabilitiesFeature — generated tools bound for SDK dispatch

Single BaseFeature carrying the whole generated surface; handlers bound per
spec method_name for AlfredClient dispatch; live get_context from the
connection's event-fed state store.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 8: StateForwarder — HA state_changed → MQTT (contract C11)

**Files:**
- Create: `app/state_forwarder.py`
- Test: `tests/test_state_forwarder.py`

**Interfaces:**
- Consumes: `StateListener` signature from Task 3 (`on_state_changed` matches it exactly so it plugs into `HAConnection.add_state_listener`); `StateChangedEvent` from `alfred_sdk.events`.
- Produces (used by Task 9):
  - `MQTT_TOPIC = "home/state_changed"` (module constant — the alfred bridge maps it to `alfred:home:state_changed`, see alfred `bus/bridge.py:26-29`)
  - `class StateForwarder`:
    - `__init__(host: str | None = None, port: int | None = None, *, queue_size: int = 1000)` — defaults from env `MQTT_HOST`/`MQTT_PORT`
    - `staticmethod build_event(entity_id, old_state, new_state, attributes) -> StateChangedEvent | None` — None only when `new_state is None` (entity removed; there is no state to represent)
    - `async on_state_changed(entity_id: str, old_state: str | None, new_state: str | None, attributes: dict[str, Any]) -> None`
    - `pending_count() -> int`
    - `async start() -> None`, `async stop() -> None`

Contract C11 pinned: payload is `StateChangedEvent(domain="home", source="home-service", entity_id=..., old_state=..., new_state=..., attributes=...).model_dump_json()`; forward ALL events, no source filtering; attribute-only updates go out with `old_state == new_state` (core gates on transitions). Resilience per spec Section 4: bounded queue (drop + WARNING when full — no unbounded buffering), reconnect-with-backoff on `MqttError`, the in-flight payload is retained across reconnects.

- [ ] **Step 1: Write the failing tests `tests/test_state_forwarder.py`**

```python
"""Tests for StateForwarder — mapping, bounded queue, MQTT publish loop."""

from __future__ import annotations

from typing import Any

import aiomqtt
from alfred_sdk.events import StateChangedEvent
import pytest

from app.state_forwarder import MQTT_TOPIC, StateForwarder
from tests.fake_ha import eventually


def test_build_event_maps_contract_c11_fields() -> None:
    event = StateForwarder.build_event(
        "light.bedroom_lamp", "on", "off", {"friendly_name": "Bedroom Lamp"}
    )
    assert event is not None
    assert event.event_type == "state_changed"
    assert event.domain == "home"
    assert event.source == "home-service"
    assert event.entity_id == "light.bedroom_lamp"
    assert event.old_state == "on"
    assert event.new_state == "off"
    assert event.attributes == {"friendly_name": "Bedroom Lamp"}


def test_build_event_attribute_only_update_forwards_equal_states() -> None:
    event = StateForwarder.build_event("light.a", "on", "on", {"brightness": 10})
    assert event is not None
    assert event.old_state == event.new_state == "on"


def test_build_event_entity_removed_skips() -> None:
    assert StateForwarder.build_event("light.a", "on", None, {}) is None


def test_build_event_new_entity_has_null_old_state() -> None:
    event = StateForwarder.build_event("light.new", None, "off", {})
    assert event is not None
    assert event.old_state is None


async def test_queue_full_drops_with_warning() -> None:
    forwarder = StateForwarder(host="mqtt-unused", port=1883, queue_size=1)
    await forwarder.on_state_changed("light.a", "on", "off", {})
    await forwarder.on_state_changed("light.b", "on", "off", {})  # dropped
    assert forwarder.pending_count() == 1


class _FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str]] = []

    async def __aenter__(self) -> _FakeMqttClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def publish(self, topic: str, payload: str) -> None:
        self.published.append((topic, str(payload)))


class _FlakyThenGoodFactory:
    """First connection attempt raises MqttError; second works."""

    def __init__(self, good: _FakeMqttClient) -> None:
        self._good = good
        self.attempts = 0

    def __call__(self, host: str, port: int) -> _FakeMqttClient:
        self.attempts += 1
        if self.attempts == 1:
            raise aiomqtt.MqttError("broker down")
        return self._good


async def test_publish_loop_publishes_event_json(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeMqttClient()
    monkeypatch.setattr("app.state_forwarder.aiomqtt.Client",
                        lambda host, port: fake)
    forwarder = StateForwarder(host="broker", port=1883)
    await forwarder.on_state_changed("light.a", "on", "off", {"friendly_name": "A"})
    await forwarder.start()
    await eventually(lambda: len(fake.published) == 1)
    topic, payload = fake.published[0]
    assert topic == MQTT_TOPIC
    event = StateChangedEvent.model_validate_json(payload)
    assert event.entity_id == "light.a"
    assert event.new_state == "off"
    await forwarder.stop()


async def test_publish_loop_retries_after_mqtt_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeMqttClient()
    factory = _FlakyThenGoodFactory(fake)
    monkeypatch.setattr("app.state_forwarder.aiomqtt.Client", factory)
    forwarder = StateForwarder(host="broker", port=1883)
    # shrink backoff for the test
    forwarder._initial_backoff = 0.02
    await forwarder.on_state_changed("light.a", "on", "off", {})
    await forwarder.start()
    await eventually(lambda: len(fake.published) == 1)
    assert factory.attempts == 2
    await forwarder.stop()


def test_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MQTT_HOST", "broker.local")
    monkeypatch.setenv("MQTT_PORT", "2883")
    forwarder = StateForwarder()
    assert forwarder._host == "broker.local"
    assert forwarder._port == 2883
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_state_forwarder.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'app.state_forwarder'`.

- [ ] **Step 3: Write `app/state_forwarder.py`**

```python
"""State forwarder — every HA state_changed → MQTT home/state_changed (contract C11).

The alfred bus bridge maps MQTT `home/state_changed` → Redis stream
`alfred:home:state_changed` (alfred bus/bridge.py mqtt_topic_to_stream_key),
feeding Reflex, triggers, and context. This retires the HA-side MQTT
automation. Forward EVERYTHING — Tier-1 visibility; SLM gating happens in core.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Any

import aiomqtt
from alfred_sdk.events import StateChangedEvent
from loguru import logger

MQTT_TOPIC = "home/state_changed"


class StateForwarder:
    """Bounded-queue MQTT publisher for state_changed events."""

    def __init__(
        self, host: str | None = None, port: int | None = None, *, queue_size: int = 1000
    ) -> None:
        self._host = host or os.getenv("MQTT_HOST", "localhost")
        self._port = port if port is not None else int(os.getenv("MQTT_PORT", "1883"))
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self._initial_backoff = 1.0
        self._max_backoff = 30.0

    @staticmethod
    def build_event(
        entity_id: str,
        old_state: str | None,
        new_state: str | None,
        attributes: dict[str, Any],
    ) -> StateChangedEvent | None:
        """Map an HA state_changed to the bus schema. None → skip (entity removed)."""
        if new_state is None:
            return None
        return StateChangedEvent(
            domain="home",
            source="home-service",
            entity_id=entity_id,
            old_state=old_state,
            new_state=new_state,
            attributes=attributes,
        )

    async def on_state_changed(
        self,
        entity_id: str,
        old_state: str | None,
        new_state: str | None,
        attributes: dict[str, Any],
    ) -> None:
        """HAConnection state listener — forward everything, no source filtering."""
        event = self.build_event(entity_id, old_state, new_state, attributes)
        if event is None:
            return
        try:
            self._queue.put_nowait(event.model_dump_json())
        except asyncio.QueueFull:
            logger.warning("MQTT forward queue full — dropping state_changed for {}", entity_id)

    def pending_count(self) -> int:
        return self._queue.qsize()

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._publish_loop(), name="state-forwarder")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(BaseException):
                await self._task
        self._task = None

    async def _publish_loop(self) -> None:
        backoff = self._initial_backoff
        inflight: str | None = None  # retained across reconnects — not lost on MqttError
        while True:
            try:
                async with aiomqtt.Client(self._host, self._port) as client:
                    backoff = self._initial_backoff
                    while True:
                        if inflight is None:
                            inflight = await self._queue.get()
                        await client.publish(MQTT_TOPIC, inflight)
                        inflight = None
            except aiomqtt.MqttError as exc:
                logger.warning("MQTT unavailable ({}) — retrying in {:.1f}s", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_state_forwarder.py -v`
Expected: `8 passed`.

- [ ] **Step 5: Lint/type-check**

Run:
```bash
uv run ruff check --fix app/state_forwarder.py tests/test_state_forwarder.py
uv run ruff format app/state_forwarder.py tests/test_state_forwarder.py
uv run mypy --strict app/state_forwarder.py
```
Expected: clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/state_forwarder.py tests/test_state_forwarder.py
git commit -m "feat: StateForwarder — every HA state_changed to MQTT home/state_changed

SDK StateChangedEvent payloads, bounded queue with drop-and-warn, MQTT
reconnect with backoff, in-flight payload retained. Retires the HA-side
MQTT automation (contract C11).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 9: Server rewrite — credentials endpoint, health, wiring, legacy deletion

**Files:**
- Rewrite: `app/server.py`
- Rewrite: `alfred_ext/register.py`
- Trim: `app/ha_client.py`
- Delete: `alfred_ext/ha_utils.py`, `alfred_ext/features/lighting.py`, `alfred_ext/features/scenes.py`, `alfred_ext/features/__init__.py`
- Rewrite test: `tests/test_server.py`
- Trim test: `tests/test_ha_client.py`

**Interfaces:**
- Consumes: everything from Tasks 3–8; `AlfredClient` (Plan 1 kwargs), `CredentialField`/`CredentialSchema` from `alfred_sdk.feature`.
- Produces:
  - `alfred_ext/register.py`: `build_credentials_schema() -> CredentialSchema`, `build_client() -> AlfredClient` (contract C1: fields `url` [type `url`, default `http://homeassistant.local:8123`] and `token` [type `password`]; `credentials_endpoint=http://{SERVICE_HOST}:8000/credentials`). The old module-level `client`/`ha` globals and import-time feature discovery are GONE.
  - `app/server.py`: `create_app() -> FastAPI` and module-level `app = create_app()` (uvicorn entry unchanged: `uvicorn app.server:app`); `class CredentialsBody` (extra="forbid", `url` defaulted, `token` required → FastAPI returns 422 for unknown/missing fields per C4); `class ContextRefresher` (debounced `client.register()` on state events — live context replaces reliance on the 5-min cycle; the 5-min re-registration loop is KEPT for tool-registry/context-TTL freshness); `health_payload(conn, index) -> dict[str, Any]` (contract C6); `async apply_env_credentials(conn) -> None` (.env dev fallback); `McpRequest`/`McpResponse` unchanged shapes.
  - App state for tests: `app.state.ha` (HAConnection), `app.state.index` (EntityIndex), `app.state.client` (AlfredClient), `app.state.forwarder` (StateForwarder), `app.state.refresher` (ContextRefresher), `app.state.capabilities_ready` (bool).

Startup semantics (spec Section 1 + 2): register with Alfred at startup even with zero features so the credentials card appears in the UI; on first successful HA connect → rebuild index → generate specs → `discover_features_from_classes` → re-register with the full manifest. On later reconnects/registry updates → rebuild index + re-register only (capability set is frozen; credential pushes pointing at a *different* HA with a different catalog need a restart — logged and documented). Registration is best-effort (no Redis → warning, service keeps serving). `POST /credentials` applies live and returns resulting health so the settings card immediately shows connected-or-failed.

- [ ] **Step 1: Write the failing tests — rewrite `tests/test_server.py` entirely**

```python
"""Integration tests for the rewritten server: /credentials, /health, /mcp, wiring."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from app.server import CredentialsBody, apply_env_credentials, create_app
from tests.fake_ha import FakeHAServer, eventually


@pytest.fixture
async def app() -> AsyncIterator[FastAPI]:
    application = create_app()
    # keep Redis out of tests — registration is best-effort by design
    application.state.client.register = AsyncMock()
    application.state.client.unregister = AsyncMock()
    yield application
    await application.state.refresher.stop()
    await application.state.ha.stop()


@pytest.fixture
async def connected_app(app: FastAPI, fake_ha: FakeHAServer) -> FastAPI:
    state = await app.state.ha.apply_credentials(fake_ha.url, fake_ha.token)
    assert state == "connected"
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_health_disconnected_before_credentials(app: FastAPI) -> None:
    async with _client(app) as http:
        resp = await http.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {
        "status": "ok",
        "service": "home-service",
        "ha": {"state": "disconnected", "entities": 0, "areas": 0, "last_event_age_s": None},
    }


async def test_credentials_endpoint_connects_and_returns_health(
    app: FastAPI, fake_ha: FakeHAServer
) -> None:
    async with _client(app) as http:
        resp = await http.post("/credentials", json={"url": fake_ha.url, "token": "test-token"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["health"]["ha"]["state"] == "connected"
    assert body["health"]["ha"]["entities"] == 11
    assert body["health"]["ha"]["areas"] == 3


async def test_credentials_bad_token_reports_auth_failed(
    app: FastAPI, fake_ha: FakeHAServer
) -> None:
    async with _client(app) as http:
        resp = await http.post("/credentials", json={"url": fake_ha.url, "token": "wrong"})
    assert resp.status_code == 200
    assert resp.json()["health"]["ha"]["state"] == "auth_failed"


async def test_credentials_unknown_field_422(app: FastAPI) -> None:
    async with _client(app) as http:
        resp = await http.post(
            "/credentials", json={"url": "http://x", "token": "t", "bogus": 1}
        )
    assert resp.status_code == 422


async def test_credentials_missing_token_422(app: FastAPI) -> None:
    async with _client(app) as http:
        resp = await http.post("/credentials", json={"url": "http://x"})
    assert resp.status_code == 422


def test_credentials_body_url_default() -> None:
    body = CredentialsBody(token="t")
    assert body.url == "http://homeassistant.local:8123"


async def test_capabilities_registered_after_connect(connected_app: FastAPI) -> None:
    assert connected_app.state.capabilities_ready is True
    connected_app.state.client.register.assert_awaited()


async def test_mcp_dispatches_generated_tool_end_to_end(
    connected_app: FastAPI, fake_ha: FakeHAServer
) -> None:
    async with _client(connected_app) as http:
        resp = await http.post("/mcp", json={
            "method": "home.light_turn_on",
            "params": {"target": "Living Room", "brightness_pct": 50},
            "id": "req-001",
        })
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "req-001"
    assert data["error"] is None
    assert data["result"]["entity_ids"] == ["light.living_room_lamp"]
    assert fake_ha.service_calls == [{
        "domain": "light", "service": "turn_on",
        "service_data": {"brightness_pct": 50},
        "target": {"entity_id": ["light.living_room_lamp"]},
    }]


async def test_mcp_unknown_method_returns_error_in_band(connected_app: FastAPI) -> None:
    async with _client(connected_app) as http:
        resp = await http.post("/mcp", json={"method": "nonexistent.tool",
                                             "params": {}, "id": "req-002"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "req-002"
    assert data["error"] is not None


async def test_mcp_unresolvable_target_returns_error_in_band(
    connected_app: FastAPI,
) -> None:
    async with _client(connected_app) as http:
        resp = await http.post("/mcp", json={
            "method": "home.light_turn_on", "params": {"target": "attic"}, "id": "req-003",
        })
    data = resp.json()
    assert data["error"] is not None
    assert "attic" in data["error"]
    assert "Areas:" in data["error"]  # LLM can self-correct from the options list


async def test_state_event_feeds_forwarder_and_health(
    connected_app: FastAPI, fake_ha: FakeHAServer
) -> None:
    # forwarder not started (no lifespan in ASGITransport) → events accumulate
    await fake_ha.push_state_changed("light.bedroom_lamp", "on", "off",
                                     {"friendly_name": "Bedroom Lamp"})
    await eventually(lambda: connected_app.state.forwarder.pending_count() == 1)
    await eventually(
        lambda: connected_app.state.ha.states["light.bedroom_lamp"].state == "off"
    )
    async with _client(connected_app) as http:
        resp = await http.get("/health")
    assert resp.json()["ha"]["last_event_age_s"] is not None


async def test_env_fallback_applies_credentials(
    app: FastAPI, fake_ha: FakeHAServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HA_HOST", fake_ha.url)
    monkeypatch.setenv("HA_TOKEN", "test-token")
    await apply_env_credentials(app.state.ha)
    assert app.state.ha.conn_state == "connected"


async def test_env_fallback_absent_stays_disconnected(
    app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HA_HOST", raising=False)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    await apply_env_credentials(app.state.ha)
    assert app.state.ha.conn_state == "disconnected"


def test_registration_manifest_carries_credentials_schema() -> None:
    from alfred_ext.register import build_client

    manifest = build_client().get_registration_manifest()
    assert manifest["service_name"] == "home-service"
    assert manifest["credentials_endpoint"].endswith(":8000/credentials")
    fields = manifest["credentials_schema"]["fields"]
    assert fields["url"]["field_type"] == "url"
    assert fields["url"]["default"] == "http://homeassistant.local:8123"
    assert fields["token"]["field_type"] == "password"
    assert fields["token"]["required"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL — `ImportError: cannot import name 'create_app' from 'app.server'` (plus old tests in the file are gone; that's intended).

- [ ] **Step 3: Rewrite `alfred_ext/register.py`**

```python
"""Alfred SDK client construction for home-service (contract C1).

The SDK is the ONLY coupling to Alfred (Pillar 2). No HA connection and no
feature discovery happen at import time — capabilities are generated at
runtime after the first successful HA connection (see app/server.py).
"""

from __future__ import annotations

import os

from alfred_sdk import AlfredClient
from alfred_sdk.feature import CredentialField, CredentialSchema


def build_credentials_schema() -> CredentialSchema:
    """Self-describing credential needs — rendered by Alfred's settings UI."""
    return CredentialSchema(
        fields={
            "url": CredentialField(
                label="Home Assistant URL",
                field_type="url",
                required=True,
                placeholder="http://192.168.50.159:8123",
                default="http://homeassistant.local:8123",
                help_text="Base URL of the Home Assistant instance.",
            ),
            "token": CredentialField(
                label="Long-lived access token",
                field_type="password",
                required=True,
                help_text="HA profile → Security → Long-lived access tokens.",
            ),
        }
    )


def build_client() -> AlfredClient:
    """Build the AlfredClient with credential metadata (contract C1)."""
    host = os.getenv("SERVICE_HOST", "localhost")
    return AlfredClient(
        service_name="home-service",
        service_endpoint=f"http://{host}:8000/mcp",
        credentials_schema=build_credentials_schema(),
        credentials_endpoint=f"http://{host}:8000/credentials",
    )
```

- [ ] **Step 4: Delete the legacy modules (name-guessing and hand-written features)**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git rm alfred_ext/ha_utils.py alfred_ext/features/lighting.py \
       alfred_ext/features/scenes.py alfred_ext/features/__init__.py
```

- [ ] **Step 5: Trim `app/ha_client.py` to the documented REST fallback (get_states only)**

Replace the whole file with:

```python
"""Thin REST fallback for Home Assistant /api/states snapshots.

The WebSocket HAConnection (app/ha_connection.py) is the primary interface.
This client is retained ONLY as a manual/debug fallback for state snapshots
per the design spec — it is not wired into the runtime.
"""

from __future__ import annotations

from typing import Any

import httpx


class HomeAssistantClient:
    """Async client for Home Assistant's REST API (states snapshot only)."""

    def __init__(self, host: str, token: str) -> None:
        self.host = host.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or create the shared long-lived httpx client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def get_states(self) -> list[dict[str, Any]]:
        """Get all entity states via REST."""
        client = self._get_client()
        resp = await client.get(f"{self.host}/api/states", headers=self.headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]
```

And replace `tests/test_ha_client.py` with:

```python
"""Tests for the REST fallback client (states snapshot only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


async def test_get_states_returns_entities() -> None:
    from app.ha_client import HomeAssistantClient

    mock_response = AsyncMock()
    mock_response.json = MagicMock(
        return_value=[
            {"entity_id": "light.living_room", "state": "on",
             "attributes": {"brightness": 255}},
            {"entity_id": "media_player.tv", "state": "playing", "attributes": {}},
        ]
    )
    mock_response.raise_for_status = lambda: None

    with patch("httpx.AsyncClient.get", return_value=mock_response):
        client = HomeAssistantClient(host="http://fake:8123", token="fake-token")
        states = await client.get_states()
    assert len(states) == 2
    assert states[0]["entity_id"] == "light.living_room"
```

- [ ] **Step 6: Rewrite `app/server.py`**

```python
"""home-service FastAPI server — MCP dispatch, credentials, health.

Composition root: wires HAConnection → EntityIndex / CapabilityGenerator /
StateForwarder / ContextRefresher, and registers the generated tool surface
with Alfred via the SDK.

The /mcp JSON-RPC contract ({method, params, id} → {id, result, error}) is
unchanged from Alfred HomeAgent's perspective.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from alfred_sdk import AlfredClient
from dotenv import load_dotenv
from fastapi import FastAPI
from loguru import logger
from pydantic import BaseModel, ConfigDict

from alfred_ext.register import build_client
from app.capability_generator import CapabilityGenerator
from app.entity_index import EntityIndex
from app.ha_connection import HAConnection
from app.home_feature import HomeCapabilitiesContext, HomeCapabilitiesFeature
from app.risk_map import RiskMap, load_reflex_config
from app.state_forwarder import StateForwarder

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Keep the periodic re-registration: refreshes the tool-registry entry and the
# 10-min-TTL context key. LIVE context freshness comes from ContextRefresher.
ENTITY_REFRESH_INTERVAL = 300.0
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


class McpRequest(BaseModel):
    """JSON-RPC style MCP tool call request."""

    method: str
    params: dict[str, Any] = {}
    id: str


class McpResponse(BaseModel):
    """JSON-RPC style MCP tool call response."""

    id: str
    result: dict[str, Any] | None = None
    error: str | None = None


class CredentialsBody(BaseModel):
    """POST /credentials body — field names match the CredentialSchema (contract C4)."""

    model_config = ConfigDict(extra="forbid")

    url: str = "http://homeassistant.local:8123"
    token: str


class ContextRefresher:
    """Debounced live context refresh — re-registers with Alfred after state events.

    Event-driven with coalescing (not polling): the first event schedules one
    refresh min_interval later; events during the window ride along.
    """

    def __init__(self, client: AlfredClient, min_interval: float = 2.0) -> None:
        self._client = client
        self._min_interval = min_interval
        self._pending: asyncio.Task[None] | None = None

    async def on_state_changed(
        self,
        entity_id: str,
        old_state: str | None,
        new_state: str | None,
        attributes: dict[str, Any],
    ) -> None:
        if self._pending is None or self._pending.done():
            self._pending = asyncio.create_task(self._refresh_soon(), name="context-refresh")

    async def _refresh_soon(self) -> None:
        await asyncio.sleep(self._min_interval)
        try:
            await self._client.register()
        except Exception as exc:
            logger.warning("Context refresh failed: {}", exc)

    async def stop(self) -> None:
        if self._pending is not None and not self._pending.done():
            self._pending.cancel()
            with contextlib.suppress(BaseException):
                await self._pending


def health_payload(conn: HAConnection, index: EntityIndex) -> dict[str, Any]:
    """Contract C6 health payload."""
    connected = conn.conn_state == "connected"
    return {
        "status": "ok",
        "service": "home-service",
        "ha": {
            "state": conn.conn_state,
            "entities": index.entity_count() if connected else 0,
            "areas": index.area_count() if connected else 0,
            "last_event_age_s": conn.last_event_age_s(),
        },
    }


async def apply_env_credentials(conn: HAConnection) -> None:
    """Dev fallback: HA_HOST/HA_TOKEN from .env when nothing has been pushed."""
    url = os.getenv("HA_HOST", "")
    token = os.getenv("HA_TOKEN", "")
    if not url or not token:
        logger.info("No HA credentials in environment — waiting for POST /credentials")
        return
    state = await conn.apply_credentials(url, token)
    logger.info("Applied HA credentials from environment — state: {}", state)


def create_app() -> FastAPI:
    conn = HAConnection()
    index = EntityIndex()
    forwarder = StateForwarder()
    client = build_client()
    generator = CapabilityGenerator(
        RiskMap.load(CONFIG_DIR / "risk_map.yaml"),
        load_reflex_config(CONFIG_DIR / "reflex_tools.yaml"),
    )
    refresher = ContextRefresher(client)

    conn.add_state_listener(forwarder.on_state_changed)
    conn.add_state_listener(refresher.on_state_changed)

    async def try_register() -> None:
        try:
            await client.register()
        except Exception as exc:
            logger.warning("Could not register with Alfred (best-effort): {}", exc)

    async def rebuild_index() -> None:
        index.rebuild(
            entity_registry=conn.entity_registry,
            device_registry=conn.device_registry,
            area_registry=conn.area_registry,
            states=conn.states,
        )

    async def refresh_loop() -> None:
        while True:
            await asyncio.sleep(ENTITY_REFRESH_INTERVAL)
            await try_register()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Register even with zero features so the credentials card appears in the UI.
        await try_register()
        await forwarder.start()
        env_task = asyncio.create_task(apply_env_credentials(conn), name="env-credentials")
        refresh_task = asyncio.create_task(refresh_loop(), name="register-refresh")
        yield
        for task in (env_task, refresh_task):
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await refresher.stop()
        await forwarder.stop()
        await conn.stop()
        try:
            await client.unregister()
        except Exception as exc:
            logger.warning("Could not unregister from Alfred: {}", exc)

    app = FastAPI(title="home-service", lifespan=lifespan)
    app.state.ha = conn
    app.state.index = index
    app.state.client = client
    app.state.forwarder = forwarder
    app.state.refresher = refresher
    app.state.capabilities_ready = False

    async def on_connect() -> None:
        await rebuild_index()
        if not app.state.capabilities_ready:
            specs = generator.generate(conn.services_catalog, index)
            ctx = HomeCapabilitiesContext(
                conn=conn, index=index, generator=generator, specs=specs
            )
            client.discover_features_from_classes([HomeCapabilitiesFeature], ctx=ctx)
            app.state.capabilities_ready = True
            logger.info(
                "Generated {} tools across {} domains from the HA service catalog",
                len(specs), len({s.domain for s in specs if s.domain}),
            )
        else:
            logger.info(
                "Reconnected to HA — capability set is frozen for this process; "
                "restart if the HA instance or its service catalog changed"
            )
        await try_register()

    async def on_registries_updated() -> None:
        await rebuild_index()
        await try_register()

    conn.add_connect_listener(on_connect)
    conn.add_registry_listener(on_registries_updated)

    @app.post("/mcp")
    async def mcp_endpoint(request: McpRequest) -> McpResponse:
        """Handle an MCP tool call — contract unchanged for Alfred's HomeAgent."""
        try:
            result = await client.dispatch(request.method, request.params)
            return McpResponse(
                id=request.id,
                result=result if isinstance(result, dict) else {"data": result},
            )
        except KeyError as exc:
            return McpResponse(id=request.id, error=str(exc))
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            logger.error("Tool execution failed: {}", message)
            return McpResponse(id=request.id, error=message)

    @app.post("/credentials")
    async def credentials_endpoint(body: CredentialsBody) -> dict[str, Any]:
        """Apply pushed credentials live; return resulting health (contract C4)."""
        state = await conn.apply_credentials(body.url, body.token)
        logger.info("Credentials applied — HA state: {}", state)
        return {"status": "ok", "health": health_payload(conn, index)}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """Contract C6 health endpoint."""
        return health_payload(conn, index)

    return app


app = create_app()
```

- [ ] **Step 7: Run the full test suite**

Run: `uv run pytest -v`
Expected: ALL tests pass (fake_ha 4, ha_connection 12, entity_index 9, risk_map 3, capability_generator 7, home_feature 8, state_forwarder 8, server 14, ha_client 1 = 66). No test may be skipped.

- [ ] **Step 8: Lint/type-check the whole source tree**

Run:
```bash
uv run ruff check --fix . && uv run ruff format .
uv run mypy --strict app alfred_ext
```
Expected: clean. (Common trip-ups: unused imports left from the rewrite; `contextlib` import missing.)

- [ ] **Step 9: Manual smoke — server boots without credentials and without Redis**

Run (macOS has no `timeout`; background + kill):
```bash
uv run uvicorn app.server:app --port 8010 & SERVER_PID=$!
sleep 2 && curl -s http://127.0.0.1:8010/health
kill $SERVER_PID
```
Expected: `{"status":"ok","service":"home-service","ha":{"state":"disconnected","entities":0,"areas":0,"last_event_age_s":null}}` and a `Could not register with Alfred (best-effort)` warning in logs if Redis is down (that is fine). NOTE: if a real `.env` with `HA_HOST`/`HA_TOKEN` exists in the repo root, the env fallback may connect to the real HA and `ha.state` will be `connected` — that is also a pass.

- [ ] **Step 10: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add app/server.py alfred_ext/register.py app/ha_client.py \
        tests/test_server.py tests/test_ha_client.py
git commit -m "feat: server rewrite — runtime credentials, C6 health, generated capabilities

POST /credentials applies live and returns health; /health reports real HA
state per contract C6; capabilities generated and registered on first HA
connect; registers with credentials_schema at startup so the UI card appears;
ContextRefresher debounces live context updates; deletes to_entity_id()
name-guessing and hand-written lighting/scenes features.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 10: Docs, Containerfile, QA backlog, final gate

**Files:**
- Modify: `Containerfile`
- Rewrite: `README.md`
- Create: `docs/qa-backlog/ha-live-discovery-smoke.md`

**Interfaces:**
- Consumes: everything shipped in Tasks 1–9.
- Produces: deployable container context (config shipped), accurate README, manual QA item per the QA Backlog convention.

- [ ] **Step 1: Update `Containerfile`**

Replace the two `COPY home-service/...` block lines so config ships and the deleted features dir is not referenced. Full new file:

```dockerfile
# home-service — HA wrapper microservice
# Build context must be the workspace root (parent of alfred/ and home-service/)
# so we can access alfred/sdk/ for the unpublished alfred-sdk package.

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Install alfred-sdk from monorepo source (not on PyPI). Installing it first
# means the alfred-sdk requirement in pyproject is already satisfied below.
COPY alfred/sdk/ /tmp/alfred-sdk/
RUN uv pip install --system --no-cache /tmp/alfred-sdk/ && rm -rf /tmp/alfred-sdk/

# Install home-service
COPY home-service/pyproject.toml /app/
COPY home-service/app/ /app/app/
COPY home-service/alfred_ext/ /app/alfred_ext/
COPY home-service/config/ /app/config/
RUN uv pip install --system --no-cache .

EXPOSE 8000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Rewrite `README.md`**

```markdown
# alfred-home-service

An async microservice that owns all Home Assistant communication for
[Alfred](https://github.com/anirudhlath/alfred). It connects to HA over the
**WebSocket API**, discovers every entity/device/area, **generates** its tool
surface from HA's own service catalog, forwards every state change onto
Alfred's event bus, and accepts credentials pushed at runtime from Alfred's UI.

Built with FastAPI + websockets + aiomqtt, packaged with
[uv](https://docs.astral.sh/uv/), Python 3.13+.

## How it fits into Alfred

```
┌──────────────┐  tool manifest + context    ┌──────────────┐                    ┌────────────────┐
│    Alfred    │ ◄────── via Redis ───────── │ home-service │  WebSocket (auth,  │ Home Assistant │
│  Home Agent  │                             │   (FastAPI)  │  events, registry, │                │
│              │ ── POST /mcp (JSON-RPC) ──► │              │  call_service) ──► │                │
│  Alfred core │ ── POST /credentials ─────► │              │                    │                │
└──────────────┘                             └──────┬───────┘                    └────────────────┘
                                                    │ every state_changed
                                                    ▼
                                       MQTT home/state_changed
                                       (bridge → alfred:home:state_changed)
```

1. **Credentials** — Alfred's settings UI shows a Home Assistant card (this
   service registers a `credentials_schema` with fields `url` + `token`).
   Saving pushes `POST /credentials {url, token}`; the service connects live
   and returns its resulting health. `.env` `HA_HOST`/`HA_TOKEN` remain a dev
   fallback. Credentials are held in memory only — on restart, Alfred's core
   re-pushes them (ServiceRegistered event).
2. **Discovery** — on connect, the service subscribes to `state_changed` and
   registry-updated events and fetches the entity/device/area registries plus
   the service catalog. The `EntityIndex` resolves areas and friendly names to
   real entity IDs (no name-guessing). Renames/additions in HA are picked up
   live; a NEW integration domain requires a service restart.
3. **Generated capabilities** — `CapabilityGenerator` crosses the service
   catalog with discovered entities. Compact `audience: reflex` tools (lights,
   switches, media players, scenes) carry live area/entity values in their
   parameter descriptions; every other domain with entities gets
   `audience: conscious` tools plus a generic `home.call_service` escape
   hatch. Risk tiers come from `config/risk_map.yaml` (data, not code).
4. **State ingest** — every `state_changed` becomes a bus-schema
   `StateChangedEvent` published to MQTT `home/state_changed`. No HA-side
   automation is required anymore.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/mcp` | POST | JSON-RPC-style tool call: `{"method": "home.light_turn_on", "params": {"target": "Living Room", "brightness_pct": 40}, "id": "req-001"}` → `{"id": "req-001", "result": {...}, "error": null}`. Errors in-band (HTTP 200). |
| `/credentials` | POST | `{"url": "...", "token": "..."}` → applies live, returns `{"status": "ok", "health": ...}`. 422 on unknown/missing fields. Trusted network only. |
| `/health` | GET | `{"status": "ok", "service": "home-service", "ha": {"state": "connected"\|"auth_failed"\|"unreachable"\|"disconnected", "entities": N, "areas": N, "last_event_age_s": ...}}` |

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `HA_HOST` | *(unset)* | Dev fallback HA base URL (UI-pushed credentials are authoritative) |
| `HA_TOKEN` | *(unset)* | Dev fallback long-lived access token |
| `SERVICE_HOST` | `localhost` | Hostname Alfred uses to reach `/mcp` and `/credentials` |
| `REDIS_URL` | `redis://localhost:6379` | Alfred's tool-registry Redis (alfred-sdk) |
| `MQTT_HOST` | `localhost` | MQTT broker for state forwarding |
| `MQTT_PORT` | `1883` | MQTT broker port |

Risk/audience tuning lives in `config/risk_map.yaml` and
`config/reflex_tools.yaml` — edit YAML, restart, no code changes.

## Run locally

```bash
uv venv --python 3.13
uv pip install -e ".[dev]" ../alfred/sdk   # adjust path to your alfred checkout
uv run uvicorn app.server:app --host 0.0.0.0 --port 8000
```

alfred-sdk is required (installed from the alfred monorepo source — not on
PyPI). Redis/MQTT are best-effort: without them the service still boots and
serves `/health`, `/credentials`, `/mcp`.

## Tests & quality gates

```bash
uv run pytest
uv run ruff check . && uv run ruff format .
uv run mypy --strict app alfred_ext
```

Tests run a fake HA WebSocket server in-process — no live Home Assistant,
Redis, or MQTT needed.

## Security notes

- The HA token is held in memory only (pushed) or read from env (dev); never
  written to disk here. Alfred core keeps the durable copy in the OS keyring.
- `/mcp` and `/credentials` are unauthenticated by design — deploy on a
  trusted private network alongside Alfred. Do not expose to the internet.

## License

MIT — see [LICENSE](LICENSE).
```

- [ ] **Step 3: Create `docs/qa-backlog/ha-live-discovery-smoke.md`**

```markdown
# Live HA Discovery + Reversible Toggle Smoke Test

**Feature:** home-service HA WebSocket discovery, generated capabilities, state ingest
**Priority:** critical
**Type:** e2e

## Prerequisites
- Real apartment Home Assistant reachable (e.g. http://192.168.50.159:8123)
- Long-lived access token minted from the HA profile page
- home-service running locally (Redis/MQTT optional for the first steps)

## Test Steps
1. `curl -s http://localhost:8000/health` — expect `ha.state = "disconnected"`.
2. `curl -s -X POST http://localhost:8000/credentials -H 'Content-Type: application/json' -d '{"url": "http://192.168.50.159:8123", "token": "<TOKEN>"}'`
3. `curl -s http://localhost:8000/health` — expect `connected` with real entity/area counts.
4. Deliberately push a WRONG token — expect `ha.state = "auth_failed"` in the response health.
5. Push the correct token again, then flip any light in the HA app; within seconds `curl -s http://localhost:8000/health` — `last_event_age_s` should reset to a small number.
6. With Mosquitto running, `mosquitto_sub -t home/state_changed -C 1` while toggling a light — expect one StateChangedEvent JSON.
7. Pick a real light and toggle it REVERSIBLY via the generated tool:
   `curl -s -X POST http://localhost:8000/mcp -d '{"method": "home.light_turn_on", "params": {"target": "<real area name>"}, "id": "qa-1"}' -H 'Content-Type: application/json'` — then turn it back off with `home.light_turn_off`.
8. Restart home-service; confirm it reports `disconnected` until credentials are re-pushed (or re-pushed automatically by Alfred core once Plans 1+3 are live).

## Expected Result
- Health transitions disconnected → connected → auth_failed → connected as driven.
- Entity/area counts match the real apartment.
- MQTT carries every state change; the light responds to /mcp calls addressed by area/friendly name.

## Notes
- Steps 1–5 and 7 need no Alfred core at all. Step 8's automatic re-push needs Plan 1 + Plan 3 core work.
- Delete this file once verified on the real apartment HA.
```

- [ ] **Step 4: FINAL GATE — run every quality check**

Run, in order, all from `/Users/anirudhlath/code/private/alfred/home-service`:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy --strict app alfred_ext
uv run pytest -q
```

Expected, respectively: `All checks passed!`; no files would be reformatted; `Success: no issues found`; all 66 tests pass, 0 failed, 0 errors, no skips. Fix anything that fails BEFORE committing — do not skip tests or loosen the mypy config to get green (fix root causes).

- [ ] **Step 5: Commit**

```bash
cd /Users/anirudhlath/code/private/alfred/home-service
git add Containerfile README.md docs/qa-backlog/ha-live-discovery-smoke.md
git commit -m "docs: README rewrite, ship config in Containerfile, live-HA QA item

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

## Spec coverage map (Section 2 + home-service side of Section 1)

| Spec requirement | Task |
|---|---|
| HAConnection: WS auth, subscribe state_changed + registry events, registry/catalog fetches, call_service over socket (C12) | 3 |
| Reconnect w/ backoff; resubscribe + refresh registries + re-register context on reconnect | 3, 9 |
| Credentials applied at runtime (`apply_credentials`), `POST /credentials` returns resulting health (C4) | 3, 9 |
| `/health` real states connected/auth_failed/unreachable/disconnected + counts + last-event age (C6) | 3, 9 |
| httpx REST client trimmed to thin `/api/states` fallback | 9 |
| EntityIndex: registries → friendly_name/domain/device_class/area/device; rebuilt on registry events; `to_entity_id()` deleted | 4, 9 |
| CapabilityGenerator: catalog × index; audience/risk tags; reflex compact tools w/ live values; conscious per-domain tools + call_service escape hatch (C9) | 5, 6, 7 |
| Risk mapping as YAML data | 5 |
| `/mcp` dispatch contract unchanged | 7, 9 |
| State forwarder: every state_changed → SDK StateChangedEvent → MQTT home/state_changed; forward all; bounded buffering (C11) | 8 |
| Live context snapshots from event flow; 5-min re-registration loop kept for TTL | 9 |
| SDK registration with credentials_schema/credentials_endpoint (C1); card appears with no credentials | 9 |
| `.env` HA_HOST/HA_TOKEN dev fallback | 9 |
| Fake HA WS server fixture + unit tests (spec Section 4 testing) | 2–9 |
| Manual QA → docs/qa-backlog | 10 |



