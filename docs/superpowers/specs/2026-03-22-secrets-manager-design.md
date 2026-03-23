# D25: Secrets Manager for Integration Credentials

**Date:** 2026-03-22
**Status:** Approved
**Scope:** PII integration credentials only (CalDAV, Robinhood). API keys (HA_TOKEN, OPENROUTER_API_KEY) remain in `.env` for now.

---

## Problem

Integration adapters (Apple Calendar, Robinhood) require PII credentials (usernames, passwords) that are currently stored in plaintext `.env` or not configured at all. There is no UI for users to configure integration credentials, and no secure storage mechanism.

## Solution

Use the `keyring` library for OS-native secure credential storage (macOS Keychain on dev, SecretService on Linux prod). Each adapter self-describes its credential requirements via a `CredentialSchema`. The `IntegrationRegistry` auto-populates credentials from keyring on first instantiation. A settings page and onboarding wizard step let users configure credentials from the web UI.

---

## 1. Credential Schema Models

New models in `core/integrations/base.py`:

```python
class CredentialField(BaseModel):
    label: str
    field_type: str = "text"  # "text" | "password" | "url"
    required: bool = True
    placeholder: str = ""
    help_text: str = ""
    transient: bool = False  # If True, value is passed to adapter but not persisted to keyring

class CredentialSchema(BaseModel):
    fields: dict[str, CredentialField]
```

The `Integration` base class gains a class variable:

The `Integration` base class gains a class attribute (following the existing pattern — `name` and `category` are already plain class attributes, not `ClassVar`):

```python
class Integration(ABC):
    name: str
    category: str
    credentials_schema: CredentialSchema = CredentialSchema(fields={})
```

Each adapter declares its schema. Field keys match `__init__` parameter names:

```python
# Apple Calendar
credentials_schema = CredentialSchema(fields={
    "caldav_url": CredentialField(
        label="CalDAV URL", field_type="url",
        placeholder="https://caldav.icloud.com",
    ),
    "username": CredentialField(
        label="Apple ID", field_type="text",
        placeholder="you@icloud.com",
    ),
    "password": CredentialField(
        label="App-Specific Password", field_type="password",
        help_text="Generate at appleid.apple.com > Sign-In and Security",
    ),
})

# Robinhood
credentials_schema = CredentialSchema(fields={
    "username": CredentialField(label="Email", placeholder="you@example.com"),
    "password": CredentialField(label="Password", field_type="password"),
    "mfa_code": CredentialField(
        label="MFA Code", required=False, transient=True,
        help_text="Optional — only needed for initial login, not stored",
    ),
})
```

Weather and Apple Health adapters have no PII credentials (Open-Meteo is keyless, Health uses a local endpoint URL) so their `credentials_schema` stays empty.

---

## 2. Secrets Store (`shared/secrets.py`)

Thin wrapper around `keyring`. All credentials stored under service name `"alfred"` with namespaced username pattern `"{integration}.{field}"`.

```python
import asyncio

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "alfred"

# --- Sync API (used by IntegrationRegistry.get() at startup) ---

def get_secret(integration: str, field: str) -> str | None:
    return keyring.get_password(SERVICE, f"{integration}.{field}")

def set_secret(integration: str, field: str, value: str) -> None:
    keyring.set_password(SERVICE, f"{integration}.{field}", value)

def delete_secret(integration: str, field: str) -> None:
    try:
        keyring.delete_password(SERVICE, f"{integration}.{field}")
    except PasswordDeleteError:
        pass

def get_all_secrets(integration: str, fields: list[str]) -> dict[str, str]:
    return {
        f: v for f in fields
        if (v := get_secret(integration, f)) is not None
    }

# --- Async wrappers (used by REST endpoints to avoid blocking the event loop) ---

async def aget_secret(integration: str, field: str) -> str | None:
    return await asyncio.to_thread(get_secret, integration, field)

async def aset_secret(integration: str, field: str, value: str) -> None:
    await asyncio.to_thread(set_secret, integration, field, value)

async def adelete_secret(integration: str, field: str) -> None:
    await asyncio.to_thread(delete_secret, integration, field)

async def aget_all_secrets(integration: str, fields: list[str]) -> dict[str, str]:
    return await asyncio.to_thread(get_all_secrets, integration, fields)
```

**Note on sync vs async:** `IntegrationRegistry.get()` is sync and called at startup (before the event loop is hot), so the sync API is appropriate there. REST endpoints must use the async wrappers (`aset_secret`, `adelete_secret`, etc.) to avoid blocking the event loop.

**Backends:**
- macOS (dev): Keychain Access (automatic)
- Linux/CachyOS (prod): SecretService via GNOME Keyring or KDE Wallet (automatic)
- Containers (CachyOS prod): Mount host D-Bus socket (`-v /run/user/1000/bus:/run/user/1000/bus`) so `keyring` uses the host's SecretService. This is preferred over `keyrings.alt` because credentials stay in the host's GNOME Keyring, shared across container restarts. Add `DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus` to the container env in `docker-compose.yml`
- Tests: Mock backend via `keyring.set_keyring()`

---

## 3. IntegrationRegistry Changes

Two additions to `core/integrations/registry.py`:

### Auto-populate from keyring

When `get()` creates a new instance and no explicit kwargs are provided, it reads `credentials_schema.fields` and fetches values from keyring:

```python
@classmethod
def get(cls, name: str, **kwargs: Any) -> Integration:
    if name in cls._instances:
        return cls._instances[name]
    integration_cls = cls._registry[name]

    # Auto-populate credentials from keyring if no kwargs provided
    if not kwargs and integration_cls.credentials_schema.fields:
        from shared.secrets import get_all_secrets
        kwargs = get_all_secrets(name, list(integration_cls.credentials_schema.fields))

    instance = integration_cls(**kwargs)
    cls._instances[name] = instance
    return instance
```

### Reconfigure

Drop cached instance and eagerly re-create with fresh keyring values. This ensures any code holding a reference to `IntegrationRegistry` (but calling `get()` each time, which is the convention) picks up new credentials immediately.

```python
@classmethod
def reconfigure(cls, name: str) -> None:
    """Drop cached instance and re-create with fresh keyring credentials."""
    cls._instances.pop(name, None)
    cls.get(name)  # eagerly rebuild
```

**Invariants:**
- Explicit kwargs override keyring (tests pass credentials directly)
- Adapters without `credentials_schema` are unaffected
- Adapters with empty keyring values degrade gracefully (existing behavior)
- All call sites use `IntegrationRegistry.get()` — no one caches adapter instances directly

---

## 4. REST API Endpoints

New endpoints on the web server (`core/channels/web_server.py`). All credential-management endpoints use the async keyring wrappers (`aset_secret`, `adelete_secret`, `aget_all_secrets`).

### Authentication

Alfred currently runs on a local network with self-declared identity claims. Until WebAuthn (D1) is implemented, credential management endpoints are restricted to **localhost-only** binding. The web server already binds to `0.0.0.0:8081` for the chat interface, so these specific endpoints use a shared `Depends(require_localhost)` FastAPI dependency that rejects requests where `request.client.host` is not `127.0.0.1` or `::1`. This is a pragmatic security boundary — not foolproof, but sufficient for a local-network dev/home system pre-WebAuthn.

### Input Validation

`PUT` endpoints validate that request body keys match the integration's `credentials_schema.fields`. Unknown fields are rejected with 422. All `required=True` fields must be present.

### `GET /api/integrations`

List all registered integrations with schema and configured status.

```json
[
  {
    "name": "apple_calendar",
    "category": "calendar",
    "schema": {
      "fields": {
        "caldav_url": {"label": "CalDAV URL", "field_type": "url", "required": true, ...},
        "username": {"label": "Apple ID", ...},
        "password": {"label": "App-Specific Password", "field_type": "password", ...}
      }
    },
    "configured": {"caldav_url": true, "username": true, "password": false}
  }
]
```

**Never returns credential values.** Only reports which fields are configured (boolean per field).

### `GET /api/integrations/{name}/status`

Run `health_check()` on the adapter.

```json
{"name": "apple_calendar", "healthy": true}
```

### `PUT /api/integrations/{name}/credentials`

Save credentials to keyring. Re-instantiates the adapter.

```json
// Request body
{"caldav_url": "https://caldav.icloud.com", "username": "me@icloud.com", "password": "xxxx"}

// Response
{"status": "ok"}
```

Fields marked `transient: True` (e.g. `mfa_code`) are passed to the adapter constructor for the current session but **not persisted to keyring**. Calls `IntegrationRegistry.reconfigure(name)` after saving so the adapter picks up new credentials.

### `DELETE /api/integrations/{name}/credentials`

Clear all credentials for this integration from keyring. Re-instantiates adapter with empty defaults.

```json
{"status": "ok"}
```

---

## 5. Frontend — Settings Page

New `web/settings.html` + `web/settings.js` page accessible via a gear icon in the header.

### Layout

- One card per integration (rendered dynamically from `GET /api/integrations`)
- Each card shows:
  - Integration name + category
  - Status indicator (unconfigured / configured / healthy / unhealthy)
  - Credential fields rendered from schema (text inputs, password inputs with show/hide toggle)
  - "Save" button → `PUT /api/integrations/{name}/credentials`
  - "Test Connection" button → `GET /api/integrations/{name}/status`
  - "Clear" button → `DELETE /api/integrations/{name}/credentials`

### Design

- Matches existing Alfred aesthetic (dark theme, Cormorant Garamond headings, warm earth tones)
- Vanilla JS — no framework (consistent with existing PWA)
- Password fields are never pre-filled with actual values; configured fields show a filled-dot placeholder

### Navigation

- Gear icon in the header bar on `index.html`
- Back arrow on `settings.html` returns to main chat
- Both pages served as static files from `web/`

---

## 6. Onboarding Wizard — Integrations Step + Skip Defaults

### New step

Inserted between "Guest access" (step 3) and "Very good, sir" (currently step 4, becomes step 5).

**Step 4 — Integrations:** Condensed integration cards with credential fields only (no test/clear buttons). Step 5 becomes the completion screen. Progress dots increase from 5 to 6.

**Credential routing:** Integration credentials entered during onboarding are saved by calling `PUT /api/integrations/{name}/credentials` directly from the frontend JS (not bundled into `OnboardingPayload`). This keeps the onboarding payload focused on preferences and reuses the same credential-saving code path as the settings page.

### Skip button on every step

Every onboarding step gets a **"Skip — use defaults"** button. Clicking it submits the onboarding payload with whatever has been filled in so far (nulls for remaining fields) and jumps to the completion screen.

### Backend defaults

`save_onboarding` treats null/missing fields as "use defaults" and writes preference files with default values. **Guard:** Only write default preference files if they do not already exist, to prevent clobbering Librarian-consolidated preferences on re-onboarding.

| Field | Default |
|-------|---------|
| `wake_time` | `"07:00"` |
| `proactivity_level` | `"moderate"` |
| `guest_controls` | `["Lighting control", "Media playback"]` |
| `work_address` | not written (truly optional) |
| `dietary_restrictions` | not written (truly optional) |
| Integration credentials | not written (adapters degrade gracefully) |

This ensures `core/memory/preferences/` always has baseline files for the MemoryReader even if the user skips everything on first run.

---

## 7. Testing Strategy

| Component | Approach |
|-----------|----------|
| `shared/secrets.py` | Unit tests with mock keyring backend (`keyring.set_keyring()`) |
| `IntegrationRegistry` auto-populate | Test `get()` reads from keyring, `reconfigure()` drops cache, explicit kwargs override keyring |
| `CredentialSchema` / `CredentialField` | Pydantic model validation, serialization |
| REST endpoints | pytest + FastAPI `TestClient`, mock keyring. Verify passwords never returned in GET |
| Onboarding defaults | Test `save_onboarding` with all-null payload writes default preference files |
| Frontend | Manual testing |

Existing adapter tests pass credentials directly via kwargs — they remain unchanged since explicit kwargs skip the keyring path.

---

## Files Changed / Created

| File | Action | Purpose |
|------|--------|---------|
| `docs/secrets.md` | Create | Architecture doc: data flow, keyring backends, container setup |
| `shared/secrets.py` | Create | Keyring wrapper |
| `core/integrations/base.py` | Modify | Add `CredentialField`, `CredentialSchema`, `credentials_schema` class attribute |
| `core/integrations/registry.py` | Modify | Auto-populate from keyring, add `reconfigure()` |
| `core/integrations/apple_calendar.py` | Modify | Add `credentials_schema` |
| `core/integrations/robinhood.py` | Modify | Add `credentials_schema` |
| `core/channels/web_server.py` | Modify | Add REST endpoints, update onboarding defaults |
| `web/settings.html` | Create | Settings page |
| `web/settings.js` | Create | Settings page logic |
| `web/settings.css` | Create | Settings page styles (or extend `style.css`) |
| `web/index.html` | Modify | Add gear icon, add integrations onboarding step, add skip buttons |
| `web/app.js` | Modify | Update onboarding flow (new step, skip logic, 6 dots) |
| `pyproject.toml` | Modify | Add `keyring` dependency |
| `tests/shared/test_secrets.py` | Create | Secrets store tests |
| `tests/core/integrations/test_registry_keyring.py` | Create | Registry keyring integration tests |
| `tests/core/channels/test_settings_api.py` | Create | Settings API endpoint tests |
| `tests/core/channels/test_onboarding_defaults.py` | Create | Onboarding defaults tests |
| `docs/backlog/remaining-work.md` | Modify | Mark D25 as DONE |

---

## Dependencies

- `keyring>=25.0` — added to main dependencies in `pyproject.toml` (not optional, since secrets are core infrastructure)

---

## Non-Goals

- API keys (HA_TOKEN, OPENROUTER_API_KEY) remain in `.env` — separate effort
- WebAuthn / voice enrollment — D1/D2
- Encryption-at-rest beyond OS keychain — OS handles this
- CLI for managing secrets — frontend-only for now
