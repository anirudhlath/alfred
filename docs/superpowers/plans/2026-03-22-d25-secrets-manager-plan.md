# D25: Secrets Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Secure PII integration credentials via OS keyring, with self-describing adapter schemas, REST API, settings UI, and onboarding integration step.

**Architecture:** `keyring` library wraps OS-native secrets (macOS Keychain / Linux SecretService). Each `Integration` adapter declares a `CredentialSchema` describing its credential fields. `IntegrationRegistry.get()` auto-populates from keyring. New REST endpoints expose CRUD. Frontend settings page and onboarding wizard step render dynamically from the schema.

**Tech Stack:** Python 3.13+, keyring, FastAPI, Pydantic v2, vanilla JS, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `shared/secrets.py` | Create | Sync + async keyring wrapper (`get_secret`, `set_secret`, `delete_secret`, `get_all_secrets` + async variants) |
| `core/integrations/base.py` | Modify | Add `CredentialField`, `CredentialSchema` models; add `credentials_schema` attribute to `Integration` ABC |
| `core/integrations/registry.py` | Modify | Auto-populate from keyring in `get()`, add `reconfigure()` |
| `core/integrations/apple_calendar.py` | Modify | Add `credentials_schema` |
| `core/integrations/robinhood.py` | Modify | Add `credentials_schema` |
| `core/channels/web_server.py` | Modify | Add `require_localhost` dependency, REST endpoints (`/api/integrations`, `/api/integrations/{name}/credentials`, `/api/integrations/{name}/status`), update `save_onboarding` with defaults guard |
| `web/settings.html` | Create | Integration settings page |
| `web/settings.js` | Create | Settings page logic (fetch schema, render cards, save/test/clear) |
| `web/index.html` | Modify | Add gear icon in header, add integrations onboarding step (step 4), add skip buttons to all steps, update to 6 progress dots |
| `web/app.js` | Modify | Update onboarding flow (skip logic, integrations step, 6 dots) |
| `web/style.css` | Modify | Add settings page styles, skip button styles |
| `pyproject.toml` | Modify | Add `keyring>=25.0` to dependencies, add mypy override for `keyring.*` |
| `docs/secrets.md` | Create | Architecture doc |
| `docs/backlog/remaining-work.md` | Modify | Mark D25 as DONE |
| `tests/conftest.py` | Modify | Add shared `InMemoryKeyring` class and `mock_keyring` autouse fixture |
| `tests/shared/test_secrets.py` | Create | Keyring wrapper unit tests |
| `tests/core/integrations/test_registry_keyring.py` | Create | Registry keyring auto-populate tests |
| `tests/core/channels/test_settings_api.py` | Create | Settings REST endpoint tests |
| `tests/core/channels/test_onboarding_defaults.py` | Create | Onboarding defaults guard tests |

---

## Task 1: Add `keyring` Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add keyring to main dependencies and mypy override**

In `pyproject.toml`, add `"keyring>=25.0"` to the `dependencies` list (after `"litellm>=1.0"`):

```toml
    "keyring>=25.0",
```

Add a mypy override section (after the `deepeval` override):

```toml
[[tool.mypy.overrides]]
module = ["keyring.*"]
ignore_missing_imports = true
```

- [ ] **Step 2: Install the new dependency**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv pip install -e ".[dev,memory,voice,integrations]"`
Expected: installs successfully, keyring resolves

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add keyring dependency for credential storage"
```

---

## Task 2: Credential Schema Models

**Files:**
- Modify: `core/integrations/base.py`
- Test: `tests/core/integrations/test_credential_schema.py`

- [ ] **Step 1: Write the failing test**

Create `tests/core/integrations/test_credential_schema.py`:

```python
"""Tests for CredentialField and CredentialSchema models."""

from __future__ import annotations

from core.integrations.base import CredentialField, CredentialSchema


def test_credential_field_defaults() -> None:
    field = CredentialField(label="Username")
    assert field.field_type == "text"
    assert field.required is True
    assert field.placeholder == ""
    assert field.help_text == ""
    assert field.transient is False


def test_credential_field_password() -> None:
    field = CredentialField(label="Secret", field_type="password", transient=True)
    assert field.field_type == "password"
    assert field.transient is True


def test_credential_schema_empty() -> None:
    schema = CredentialSchema(fields={})
    assert schema.fields == {}
    data = schema.model_dump()
    assert data == {"fields": {}}


def test_credential_schema_with_fields() -> None:
    schema = CredentialSchema(fields={
        "username": CredentialField(label="Email", placeholder="you@example.com"),
        "password": CredentialField(label="Password", field_type="password"),
    })
    assert len(schema.fields) == 2
    assert schema.fields["username"].label == "Email"
    assert schema.fields["password"].field_type == "password"


def test_credential_schema_serialization() -> None:
    """Schema should serialize cleanly for REST API responses."""
    schema = CredentialSchema(fields={
        "url": CredentialField(label="URL", field_type="url", required=True),
    })
    data = schema.model_dump()
    assert data["fields"]["url"]["label"] == "URL"
    assert data["fields"]["url"]["field_type"] == "url"
    assert data["fields"]["url"]["transient"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/test_credential_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'CredentialField'`

- [ ] **Step 3: Implement the models**

Edit `core/integrations/base.py`. Add these two models after the existing `IntegrationCapability` class (before the `Integration` ABC):

```python
class CredentialField(BaseModel):
    """Describes one credential input field for an integration adapter."""

    label: str
    field_type: str = "text"  # "text" | "password" | "url"
    required: bool = True
    placeholder: str = ""
    help_text: str = ""
    transient: bool = False  # If True, value is passed to adapter but not persisted


class CredentialSchema(BaseModel):
    """Describes all credential fields for an integration adapter."""

    fields: dict[str, CredentialField]
```

Add `credentials_schema` to the `Integration` ABC (after the `category` annotation):

```python
    credentials_schema: CredentialSchema = CredentialSchema(fields={})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/test_credential_schema.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run existing integration tests to verify no regression**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/ core/integrations/ -v`
Expected: all existing tests PASS

- [ ] **Step 6: Type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict core/integrations/base.py`
Expected: PASS with no errors

- [ ] **Step 7: Commit**

```bash
git add core/integrations/base.py tests/core/integrations/test_credential_schema.py
git commit -m "feat: add CredentialField and CredentialSchema models"
```

---

## Task 3: Shared Test Fixture + Secrets Store (`shared/secrets.py`)

**Files:**
- Modify: `tests/conftest.py`
- Create: `shared/secrets.py`
- Test: `tests/shared/test_secrets.py`

- [ ] **Step 0: Create shared InMemoryKeyring fixture in conftest**

Check if `tests/conftest.py` exists. If it does, append to it. If not, create it. Add:

```python
import keyring
import keyring.backend
import pytest


class InMemoryKeyring(keyring.backend.KeyringBackend):
    """In-memory keyring backend for testing. Shared across test modules."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[str, dict[str, str]] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get(service, {}).get(username)

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store.setdefault(service, {})[username] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[service][username]
        except KeyError:
            from keyring.errors import PasswordDeleteError

            raise PasswordDeleteError(username)


@pytest.fixture(autouse=True)
def _mock_keyring() -> None:
    """Install fresh in-memory keyring backend before each test."""
    keyring.set_keyring(InMemoryKeyring())
```

**Note:** This `autouse=True` fixture means ALL tests in the suite get a mock keyring. This is safe — no test should depend on the real OS keyring. Existing tests don't import `keyring` so they're unaffected.

- [ ] **Step 1: Write the failing tests**

Create `tests/shared/test_secrets.py`:

```python
"""Tests for shared.secrets — keyring wrapper."""

from __future__ import annotations

import pytest

from shared.secrets import (
    adelete_secret,
    aget_all_secrets,
    aget_secret,
    aset_secret,
    delete_secret,
    get_all_secrets,
    get_secret,
    set_secret,
)


def test_set_and_get_secret() -> None:
    set_secret("test_integration", "username", "alice")
    assert get_secret("test_integration", "username") == "alice"


def test_get_secret_not_found() -> None:
    assert get_secret("nonexistent", "field") is None


def test_delete_secret() -> None:
    set_secret("test_integration", "password", "s3cret")
    delete_secret("test_integration", "password")
    assert get_secret("test_integration", "password") is None


def test_delete_secret_nonexistent_no_error() -> None:
    """Deleting a nonexistent secret should not raise."""
    delete_secret("nonexistent", "field")


def test_get_all_secrets() -> None:
    set_secret("cal", "url", "https://caldav.example.com")
    set_secret("cal", "user", "bob")
    result = get_all_secrets("cal", ["url", "user", "password"])
    assert result == {"url": "https://caldav.example.com", "user": "bob"}
    assert "password" not in result


def test_get_all_secrets_empty() -> None:
    result = get_all_secrets("empty", ["a", "b"])
    assert result == {}


@pytest.mark.asyncio
async def test_async_set_and_get() -> None:
    await aset_secret("async_test", "key", "value")
    result = await aget_secret("async_test", "key")
    assert result == "value"


@pytest.mark.asyncio
async def test_async_delete() -> None:
    await aset_secret("async_test", "key", "value")
    await adelete_secret("async_test", "key")
    result = await aget_secret("async_test", "key")
    assert result is None


@pytest.mark.asyncio
async def test_async_get_all() -> None:
    await aset_secret("async_all", "a", "1")
    await aset_secret("async_all", "b", "2")
    result = await aget_all_secrets("async_all", ["a", "b", "c"])
    assert result == {"a": "1", "b": "2"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shared.secrets'`

- [ ] **Step 3: Implement shared/secrets.py**

Create `shared/secrets.py`:

```python
"""Keyring-based secrets store for integration credentials.

Wraps the `keyring` library to provide sync and async access to OS-native
credential storage (macOS Keychain, Linux SecretService).

Sync API is used by IntegrationRegistry.get() at startup.
Async API (a-prefixed) is used by REST endpoints to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "alfred"


# --- Sync API ---


def get_secret(integration: str, field: str) -> str | None:
    """Retrieve a credential field from the OS keyring. Returns None if not set."""
    return keyring.get_password(SERVICE, f"{integration}.{field}")


def set_secret(integration: str, field: str, value: str) -> None:
    """Store a credential field in the OS keyring."""
    keyring.set_password(SERVICE, f"{integration}.{field}", value)


def delete_secret(integration: str, field: str) -> None:
    """Remove a credential field from the OS keyring. No-op if not found."""
    try:
        keyring.delete_password(SERVICE, f"{integration}.{field}")
    except PasswordDeleteError:
        pass


def get_all_secrets(integration: str, fields: list[str]) -> dict[str, str]:
    """Fetch all credential fields for an integration. Returns only non-None values."""
    return {
        f: v
        for f in fields
        if (v := get_secret(integration, f)) is not None
    }


# --- Async wrappers (for REST endpoints) ---


async def aget_secret(integration: str, field: str) -> str | None:
    """Async version of get_secret."""
    return await asyncio.to_thread(get_secret, integration, field)


async def aset_secret(integration: str, field: str, value: str) -> None:
    """Async version of set_secret."""
    await asyncio.to_thread(set_secret, integration, field, value)


async def adelete_secret(integration: str, field: str) -> None:
    """Async version of delete_secret."""
    await asyncio.to_thread(delete_secret, integration, field)


async def aget_all_secrets(integration: str, fields: list[str]) -> dict[str, str]:
    """Async version of get_all_secrets."""
    return await asyncio.to_thread(get_all_secrets, integration, fields)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/shared/test_secrets.py -v`
Expected: all 10 tests PASS

- [ ] **Step 5: Type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict shared/secrets.py`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shared/secrets.py tests/shared/test_secrets.py
git commit -m "feat: add keyring-based secrets store (shared/secrets.py)"
```

---

## Task 4: Adapter Credential Schemas

**Files:**
- Modify: `core/integrations/apple_calendar.py`
- Modify: `core/integrations/robinhood.py`
- Test: existing tests in `tests/core/integrations/test_apple_calendar.py` and `tests/core/integrations/test_robinhood.py`

- [ ] **Step 1: Write tests for schema presence**

Add to bottom of `tests/core/integrations/test_apple_calendar.py`:

```python
def test_credentials_schema_declared() -> None:
    """Adapter should declare its credential fields."""
    schema = AppleCalendarAdapter.credentials_schema
    assert "caldav_url" in schema.fields
    assert "username" in schema.fields
    assert "password" in schema.fields
    assert schema.fields["password"].field_type == "password"
    assert schema.fields["caldav_url"].field_type == "url"
```

Add to bottom of `tests/core/integrations/test_robinhood.py`:

```python
def test_credentials_schema_declared() -> None:
    """Adapter should declare its credential fields."""
    schema = RobinhoodAdapter.credentials_schema
    assert "username" in schema.fields
    assert "password" in schema.fields
    assert "mfa_code" in schema.fields
    assert schema.fields["password"].field_type == "password"
    assert schema.fields["mfa_code"].required is False
    assert schema.fields["mfa_code"].transient is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/test_apple_calendar.py::test_credentials_schema_declared tests/core/integrations/test_robinhood.py::test_credentials_schema_declared -v`
Expected: FAIL — `credentials_schema` has no fields

- [ ] **Step 3: Add schema to Apple Calendar adapter**

Edit `core/integrations/apple_calendar.py`. Add import of `CredentialField, CredentialSchema` from `core.integrations.base`. Add inside the class body, after `category = "calendar"`:

```python
    credentials_schema = CredentialSchema(fields={
        "caldav_url": CredentialField(
            label="CalDAV URL",
            field_type="url",
            placeholder="https://caldav.icloud.com",
        ),
        "username": CredentialField(
            label="Apple ID",
            placeholder="you@icloud.com",
        ),
        "password": CredentialField(
            label="App-Specific Password",
            field_type="password",
            help_text="Generate at appleid.apple.com > Sign-In and Security",
        ),
    })
```

- [ ] **Step 4: Add schema to Robinhood adapter**

Edit `core/integrations/robinhood.py`. Add import of `CredentialField, CredentialSchema` from `core.integrations.base`. Add inside the class body, after `category = "finance"`:

```python
    credentials_schema = CredentialSchema(fields={
        "username": CredentialField(
            label="Email",
            placeholder="you@example.com",
        ),
        "password": CredentialField(
            label="Password",
            field_type="password",
        ),
        "mfa_code": CredentialField(
            label="MFA Code",
            required=False,
            transient=True,
            help_text="Optional — only needed for initial login, not stored",
        ),
    })
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/ -v`
Expected: all tests PASS (including existing ones)

- [ ] **Step 6: Type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict core/integrations/apple_calendar.py core/integrations/robinhood.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/integrations/apple_calendar.py core/integrations/robinhood.py tests/core/integrations/test_apple_calendar.py tests/core/integrations/test_robinhood.py
git commit -m "feat: add credentials_schema to Apple Calendar and Robinhood adapters"
```

---

## Task 5: IntegrationRegistry Keyring Auto-Populate + Reconfigure

**Files:**
- Modify: `core/integrations/registry.py`
- Test: `tests/core/integrations/test_registry_keyring.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/integrations/test_registry_keyring.py`:

```python
"""Tests for IntegrationRegistry keyring auto-population.

Uses InMemoryKeyring from tests/conftest.py (autouse fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry


class _CredsAdapter(Integration):
    """Test adapter that captures credentials."""

    name = "creds_test"
    category = "test"
    credentials_schema = CredentialSchema(fields={
        "username": CredentialField(label="User"),
        "password": CredentialField(label="Pass", field_type="password"),
    })

    def __init__(self, username: str = "", password: str = "") -> None:
        self.username = username
        self.password = password

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return bool(self.username)


class _NoCredsAdapter(Integration):
    """Test adapter with no credentials_schema."""

    name = "no_creds"
    category = "test"

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return True


@pytest.fixture(autouse=True)
def _reset_registry() -> None:
    """Reset registry before each test. Keyring mock is handled by conftest."""
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["creds_test"] = _CredsAdapter
    IntegrationRegistry._registry["no_creds"] = _NoCredsAdapter


def test_get_auto_populates_from_keyring() -> None:
    """get() should read credentials from keyring when no kwargs provided."""
    from shared.secrets import set_secret

    set_secret("creds_test", "username", "alice")
    set_secret("creds_test", "password", "s3cret")

    adapter = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == "alice"
    assert adapter.password == "s3cret"


def test_get_explicit_kwargs_override_keyring() -> None:
    """Explicit kwargs should override keyring values."""
    from shared.secrets import set_secret

    set_secret("creds_test", "username", "keyring_user")

    adapter = IntegrationRegistry.get("creds_test", username="explicit_user", password="explicit_pass")
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == "explicit_user"
    assert adapter.password == "explicit_pass"


def test_get_empty_keyring_degrades_gracefully() -> None:
    """Adapter should work with empty keyring (empty string defaults)."""
    adapter = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter, _CredsAdapter)
    assert adapter.username == ""
    assert adapter.password == ""


def test_get_no_schema_adapter_unaffected() -> None:
    """Adapter without credentials_schema should instantiate normally."""
    adapter = IntegrationRegistry.get("no_creds")
    assert isinstance(adapter, _NoCredsAdapter)


def test_reconfigure_drops_cache_and_rebuilds() -> None:
    """reconfigure() should drop cached instance and rebuild with fresh keyring."""
    from shared.secrets import set_secret

    # First instantiation — empty
    adapter1 = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter1, _CredsAdapter)
    assert adapter1.username == ""

    # Set credentials
    set_secret("creds_test", "username", "bob")
    set_secret("creds_test", "password", "new_pass")

    # Reconfigure
    IntegrationRegistry.reconfigure("creds_test")

    # New instance should have fresh credentials
    adapter2 = IntegrationRegistry.get("creds_test")
    assert isinstance(adapter2, _CredsAdapter)
    assert adapter2.username == "bob"
    assert adapter2.password == "new_pass"
    assert adapter2 is not adapter1


def test_reconfigure_not_instantiated_no_error() -> None:
    """reconfigure() on registered but not-yet-instantiated adapter should not raise."""
    IntegrationRegistry.reconfigure("no_creds")


def test_reconfigure_unregistered_no_error() -> None:
    """reconfigure() on truly unregistered name should not raise."""
    IntegrationRegistry.reconfigure("truly_nonexistent")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/test_registry_keyring.py -v`
Expected: FAIL — `reconfigure` not found, keyring not read in `get()`

- [ ] **Step 3: Modify IntegrationRegistry**

Edit `core/integrations/registry.py`.

Update the `get()` method to auto-populate from keyring. Merge this logic into the existing method (preserving the `try/except KeyError` error handling):

```python
    @classmethod
    def get(cls, name: str, **kwargs: Any) -> Integration:
        """Look up an integration by name. Creates instance on first access.

        Pass kwargs to configure the adapter on first instantiation (e.g.,
        latitude=40.7 for weather). Subsequent calls return the cached instance.

        If no kwargs are provided and the adapter declares a credentials_schema,
        credentials are auto-populated from the OS keyring.
        Raises KeyError if unknown.
        """
        if name in cls._instances:
            return cls._instances[name]
        try:
            integration_cls = cls._registry[name]
        except KeyError:
            raise KeyError(
                f"Unknown integration: {name!r}. Available: {list(cls._registry.keys())}"
            ) from None

        # Auto-populate credentials from keyring if no kwargs provided
        if not kwargs and integration_cls.credentials_schema.fields:
            from shared.secrets import get_all_secrets

            kwargs = get_all_secrets(name, list(integration_cls.credentials_schema.fields))

        instance = integration_cls(**kwargs)
        cls._instances[name] = instance
        return instance
```

Add the `reconfigure()` method:

```python
    @classmethod
    def reconfigure(cls, name: str) -> None:
        """Drop cached instance and re-create with fresh keyring credentials."""
        cls._instances.pop(name, None)
        if name in cls._registry:
            cls.get(name)  # eagerly rebuild
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/test_registry_keyring.py tests/core/integrations/test_registry.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run full integration test suite for regression**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/integrations/ core/integrations/ -v`
Expected: all tests PASS

- [ ] **Step 6: Type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict core/integrations/registry.py`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/integrations/registry.py tests/core/integrations/test_registry_keyring.py
git commit -m "feat: auto-populate integration credentials from OS keyring"
```

---

## Task 6: REST API Endpoints

**Files:**
- Modify: `core/channels/web_server.py`
- Test: `tests/core/channels/test_settings_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/channels/test_settings_api.py`:

```python
"""Tests for integration settings REST API endpoints.

Uses InMemoryKeyring from tests/conftest.py (autouse fixture).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry


class _TestAdapter(Integration):
    name = "test_adapter"
    category = "testing"
    credentials_schema = CredentialSchema(fields={
        "api_url": CredentialField(label="API URL", field_type="url"),
        "token": CredentialField(label="Token", field_type="password"),
        "mfa": CredentialField(label="MFA", required=False, transient=True),
    })

    def __init__(self, api_url: str = "", token: str = "", mfa: str = "") -> None:
        self.api_url = api_url
        self.token = token

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return []

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        return IntegrationResult(data={}, freshness=datetime.now(UTC), confidence=0.0)

    async def health_check(self) -> bool:
        return bool(self.api_url)


@pytest.fixture(autouse=True)
def _setup() -> None:
    """Reset registry. Keyring mock is handled by conftest."""
    IntegrationRegistry._registry.clear()
    IntegrationRegistry._instances.clear()
    IntegrationRegistry._registry["test_adapter"] = _TestAdapter


def _client() -> TestClient:
    from unittest.mock import AsyncMock
    from core.channels.web_server import create_app

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    return TestClient(app)


def test_list_integrations() -> None:
    client = _client()
    resp = client.get("/api/integrations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 1
    adapter = next(a for a in data if a["name"] == "test_adapter")
    assert adapter["category"] == "testing"
    assert "api_url" in adapter["schema"]["fields"]
    assert "token" in adapter["schema"]["fields"]
    # Credentials not configured yet
    assert adapter["configured"]["api_url"] is False
    assert adapter["configured"]["token"] is False


def test_list_integrations_never_returns_values() -> None:
    """GET /api/integrations must never return actual credential values."""
    from shared.secrets import set_secret
    set_secret("test_adapter", "token", "super_secret_value")

    client = _client()
    resp = client.get("/api/integrations")
    body = resp.text
    assert "super_secret_value" not in body


def test_save_credentials() -> None:
    client = _client()
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://api.example.com", "token": "abc123"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    # Verify stored in keyring
    from shared.secrets import get_secret
    assert get_secret("test_adapter", "api_url") == "https://api.example.com"
    assert get_secret("test_adapter", "token") == "abc123"


def test_save_credentials_transient_not_stored() -> None:
    """Transient fields should not be persisted to keyring."""
    client = _client()
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com", "token": "t", "mfa": "123456"},
    )
    assert resp.status_code == 200

    from shared.secrets import get_secret
    assert get_secret("test_adapter", "mfa") is None


def test_save_credentials_unknown_fields_rejected() -> None:
    client = _client()
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com", "token": "t", "bogus": "value"},
    )
    assert resp.status_code == 422


def test_save_credentials_missing_required_rejected() -> None:
    client = _client()
    resp = client.put(
        "/api/integrations/test_adapter/credentials",
        json={"api_url": "https://x.com"},  # missing required "token"
    )
    assert resp.status_code == 422


def test_save_credentials_unknown_integration() -> None:
    client = _client()
    resp = client.put(
        "/api/integrations/nonexistent/credentials",
        json={"key": "value"},
    )
    assert resp.status_code == 404


def test_delete_credentials() -> None:
    from shared.secrets import set_secret
    set_secret("test_adapter", "api_url", "https://old.com")
    set_secret("test_adapter", "token", "old_token")

    client = _client()
    resp = client.delete("/api/integrations/test_adapter/credentials")
    assert resp.status_code == 200

    from shared.secrets import get_secret
    assert get_secret("test_adapter", "api_url") is None
    assert get_secret("test_adapter", "token") is None


def test_health_check_endpoint() -> None:
    from shared.secrets import set_secret
    set_secret("test_adapter", "api_url", "https://api.example.com")
    set_secret("test_adapter", "token", "t")

    # Reconfigure so adapter picks up keyring credentials
    IntegrationRegistry.reconfigure("test_adapter")

    client = _client()
    resp = client.get("/api/integrations/test_adapter/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "test_adapter"
    assert data["healthy"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_settings_api.py -v`
Expected: FAIL — routes not found (404)

- [ ] **Step 3: Implement the localhost dependency and REST endpoints**

Edit `core/channels/web_server.py`. Add these imports near the top:

```python
import asyncio

from fastapi import Depends, HTTPException, Request
```

Add the `require_localhost` dependency function (before `create_app`):

```python
async def require_localhost(request: Request) -> None:
    """FastAPI dependency — restrict endpoint to localhost only."""
    client_host = request.client.host if request.client else ""
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="Localhost access only")
```

Note: `"testclient"` is included because FastAPI's `TestClient` uses that as the host.

Inside `create_app()`, add the four endpoints before the static mount:

```python
    @app.get("/api/integrations")
    async def list_integrations() -> list[dict[str, Any]]:
        """List all integrations with schema and configured status."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import aget_secret

        result: list[dict[str, Any]] = []
        for name in IntegrationRegistry.available():
            integration_cls = IntegrationRegistry._registry[name]
            schema = integration_cls.credentials_schema
            configured: dict[str, bool] = {}
            for field_name in schema.fields:
                val = await aget_secret(name, field_name)
                configured[field_name] = val is not None
            result.append({
                "name": name,
                "category": integration_cls.category,
                "schema": schema.model_dump(),
                "configured": configured,
            })
        return result

    @app.put(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_localhost)],
    )
    async def save_credentials(name: str, request: Request) -> dict[str, str]:
        """Save integration credentials to OS keyring."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import adelete_secret, aset_secret

        if name not in IntegrationRegistry._registry:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")

        integration_cls = IntegrationRegistry._registry[name]
        schema = integration_cls.credentials_schema
        body: dict[str, str] = await request.json()

        # Validate: reject unknown fields
        unknown = set(body.keys()) - set(schema.fields.keys())
        if unknown:
            raise HTTPException(status_code=422, detail=f"Unknown fields: {unknown}")

        # Validate: check required fields
        missing = [
            f for f, field in schema.fields.items()
            if field.required and f not in body and not field.transient
        ]
        if missing:
            raise HTTPException(status_code=422, detail=f"Missing required fields: {missing}")

        # Store non-transient fields in keyring
        for field_name, value in body.items():
            field_def = schema.fields[field_name]
            if field_def.transient:
                continue
            await aset_secret(name, field_name, value)

        # Reconfigure adapter with fresh credentials (sync — uses keyring internally)
        await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    @app.delete(
        "/api/integrations/{name}/credentials",
        dependencies=[Depends(require_localhost)],
    )
    async def delete_credentials(name: str) -> dict[str, str]:
        """Clear all credentials for an integration from OS keyring."""
        from core.integrations.registry import IntegrationRegistry
        from shared.secrets import adelete_secret

        if name not in IntegrationRegistry._registry:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")

        schema = IntegrationRegistry._registry[name].credentials_schema
        for field_name in schema.fields:
            await adelete_secret(name, field_name)

        await asyncio.to_thread(IntegrationRegistry.reconfigure, name)
        return {"status": "ok"}

    @app.get("/api/integrations/{name}/status")
    async def integration_status(name: str) -> dict[str, Any]:
        """Run health check on an integration adapter."""
        from core.integrations.registry import IntegrationRegistry

        if name not in IntegrationRegistry._registry:
            raise HTTPException(status_code=404, detail=f"Unknown integration: {name}")

        try:
            instance = IntegrationRegistry.get(name)
            healthy = await instance.health_check()
        except Exception:
            healthy = False
        return {"name": name, "healthy": healthy}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_settings_api.py -v`
Expected: all tests PASS

- [ ] **Step 5: Run existing web server tests for regression**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_web_server.py -v`
Expected: all existing tests PASS

- [ ] **Step 6: Type check**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict core/channels/web_server.py`
Expected: PASS (some existing `# type: ignore` lines may remain)

- [ ] **Step 7: Commit**

```bash
git add core/channels/web_server.py tests/core/channels/test_settings_api.py
git commit -m "feat: add REST API for integration credential management"
```

---

## Task 7: Onboarding Defaults Guard

**Files:**
- Modify: `core/channels/web_server.py`
- Test: `tests/core/channels/test_onboarding_defaults.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/channels/test_onboarding_defaults.py`:

```python
"""Tests for onboarding default preference writing."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from core.channels.web_server import _atomic_write, _preference_file


def test_onboarding_writes_defaults_for_null_fields(tmp_path: Path) -> None:
    """When wake_time is null, defaults should be written."""
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    from core.channels.web_server import create_app

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    import core.channels.web_server as ws
    written: dict[str, str] = {}
    orig = ws._atomic_write

    def capture(path: Path, content: str) -> None:
        written[path.name] = content
        orig(path, content)

    with (
        patch.object(ws, "_atomic_write", side_effect=capture),
        patch.object(ws, "_get_prefs_dirs", return_value=(prefs_dir, profile_dir)),
    ):
        client = TestClient(app)
        resp = client.post("/api/onboarding", json={})

    assert resp.status_code == 200
    # Defaults should have been written
    assert "personal.md" in written
    assert "07:00" in written["personal.md"]
    assert "proactivity.md" in written
    assert "moderate" in written["proactivity.md"]


def test_onboarding_does_not_overwrite_existing(tmp_path: Path) -> None:
    """If preference files already exist, defaults should NOT overwrite them."""
    from fastapi.testclient import TestClient
    from unittest.mock import AsyncMock
    from core.channels.web_server import create_app

    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()

    prefs_dir = tmp_path / "preferences"
    profile_dir = tmp_path / "profile"
    prefs_dir.mkdir(parents=True)
    profile_dir.mkdir(parents=True)

    # Pre-existing files
    (prefs_dir / "personal.md").write_text("existing content")
    (profile_dir / "proactivity.md").write_text("existing proactivity")

    import core.channels.web_server as ws
    written: dict[str, str] = {}
    orig = ws._atomic_write

    def capture(path: Path, content: str) -> None:
        written[path.name] = content
        orig(path, content)

    with (
        patch.object(ws, "_atomic_write", side_effect=capture),
        patch.object(ws, "_get_prefs_dirs", return_value=(prefs_dir, profile_dir)),
    ):
        client = TestClient(app)
        resp = client.post("/api/onboarding", json={})

    assert resp.status_code == 200
    # Should NOT have overwritten existing files
    assert "personal.md" not in written
    assert "proactivity.md" not in written
    assert (prefs_dir / "personal.md").read_text() == "existing content"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_onboarding_defaults.py -v`
Expected: FAIL — `_get_prefs_dirs` not found, defaults not written for null payload

- [ ] **Step 3: Refactor save_onboarding to support defaults and guard**

Edit `core/channels/web_server.py`:

1. Extract the prefs/profile dir computation into a helper (so tests can patch it):

```python
def _get_prefs_dirs() -> tuple[Path, Path]:
    """Return (preferences_dir, profile_dir) for semantic memory."""
    base = Path(__file__).resolve().parent.parent / "memory"
    return base / "preferences", base / "profile"
```

2. Update `save_onboarding` to use `_get_prefs_dirs()`, write defaults for null fields, and guard against overwriting existing files:

```python
    @app.post("/api/onboarding")
    async def save_onboarding(payload: OnboardingPayload) -> dict[str, str]:
        """Save onboarding preferences to semantic memory files.

        Writes default values for any null fields. Skips writing if the
        preference file already exists (prevents clobbering Librarian data).
        """
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        prefs_dir, profile_dir = _get_prefs_dirs()
        prefs_dir.mkdir(parents=True, exist_ok=True)
        profile_dir.mkdir(parents=True, exist_ok=True)

        # Personal preferences (with defaults)
        personal_path = prefs_dir / "personal.md"
        if not personal_path.exists():
            wake = payload.wake_time or "07:00"
            lines: list[str] = [f"- Usual wake time: {wake}"]
            if payload.work_address:
                lines.append(f"- Work address: {payload.work_address}")
            if payload.dietary_restrictions:
                lines.append(f"- Dietary restrictions: {payload.dietary_restrictions}")
            _atomic_write(
                personal_path,
                _preference_file("general", today, "manual", "Personal", lines),
            )

        # Proactivity level (with default)
        proactivity_path = profile_dir / "proactivity.md"
        if not proactivity_path.exists():
            level = payload.proactivity_level or "moderate"
            _atomic_write(
                proactivity_path,
                _preference_file(
                    "general", today, "manual", "Proactivity Level",
                    [f"- Level: {level}"],
                ),
            )

        # Guest mode config (with defaults)
        guest_path = prefs_dir / "guest_mode.md"
        if not guest_path.exists():
            controls = payload.guest_controls or ["Lighting control", "Media playback"]
            guest_lines = [f"- {ctrl}" for ctrl in controls]
            _atomic_write(
                guest_path,
                _preference_file("general", today, "manual", "Guest Mode", guest_lines),
            )

        n_fields = len(payload.model_dump(exclude_none=True))
        logger.info("Onboarding preferences saved ({} fields)", n_fields)
        return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest tests/core/channels/test_onboarding_defaults.py tests/core/channels/test_web_server.py -v`
Expected: all tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/channels/web_server.py tests/core/channels/test_onboarding_defaults.py
git commit -m "feat: onboarding writes defaults for skipped fields, guards existing files"
```

---

## Task 8: Frontend — Settings Page

**Files:**
- Create: `web/settings.html`
- Create: `web/settings.js`
- Modify: `web/style.css`
- Modify: `web/index.html`

- [ ] **Step 1: Create settings.html**

Create `web/settings.html` — the integration settings page. Structure:

- Same `<head>` as `index.html` (fonts, style.css)
- Header with back arrow + "Settings" title + brass rules
- `<main id="integrations">` container for dynamically rendered cards
- Links to `settings.js`

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="theme-color" content="#1a1612">
    <title>Alfred — Settings</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Cormorant+Garamond:ital,wght@0,400;0,500;0,600;1,400&family=EB+Garamond:ital,wght@0,400;0,500;1,400&family=JetBrains+Mono:wght@400&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="style.css">
</head>
<body>
    <div id="app" class="settings-page">
        <header>
            <div class="header-rule"></div>
            <div class="header-content">
                <a href="/" class="back-link" title="Back to chat">
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                        <polyline points="15 18 9 12 15 6"/>
                    </svg>
                </a>
                <h1>Settings</h1>
                <div class="header-meta"></div>
            </div>
            <div class="header-rule"></div>
        </header>

        <main id="integrations">
            <h2 class="settings-section-title">Integrations</h2>
            <p class="settings-desc">Configure credentials for external services. Stored securely in your system keychain.</p>
            <div id="integration-cards"></div>
        </main>
    </div>
    <script src="settings.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create settings.js**

Create `web/settings.js` — fetches schema from `/api/integrations`, renders a card per integration with credential fields, handles save/test/clear.

```javascript
// Alfred Settings — Integration Credential Management

const cardsContainer = document.getElementById('integration-cards');

async function loadIntegrations() {
    try {
        const resp = await fetch('/api/integrations');
        const integrations = await resp.json();
        cardsContainer.innerHTML = '';
        integrations.forEach(renderCard);
    } catch (err) {
        cardsContainer.innerHTML = '<p class="settings-error">Failed to load integrations.</p>';
        console.error('Failed to load integrations:', err);
    }
}

function renderCard(integration) {
    const card = document.createElement('div');
    card.className = 'integration-card';
    card.dataset.name = integration.name;

    const fields = integration.schema.fields;
    const fieldNames = Object.keys(fields);

    // Status: check if all required fields are configured
    const allConfigured = fieldNames
        .filter(f => fields[f].required && !fields[f].transient)
        .every(f => integration.configured[f]);

    const statusClass = allConfigured ? 'configured' : 'unconfigured';
    const statusText = allConfigured ? 'Configured' : 'Not configured';

    let fieldsHtml = '';
    for (const [name, field] of Object.entries(fields)) {
        const inputType = field.field_type === 'password' ? 'password' : 'text';
        const required = field.required ? 'required' : '';
        const configured = integration.configured[name];
        const placeholder = configured
            ? (field.field_type === 'password' ? '••••••••' : field.placeholder)
            : field.placeholder;
        const helpHtml = field.help_text
            ? `<span class="field-help">${field.help_text}</span>`
            : '';
        const transientBadge = field.transient
            ? '<span class="field-badge">not stored</span>'
            : '';

        fieldsHtml += `
            <label class="settings-label">
                <span class="label-text">${field.label}${transientBadge}</span>
                <div class="input-wrapper">
                    <input type="${inputType}" name="${name}" placeholder="${placeholder}"
                           ${required} autocomplete="off" data-field="${name}">
                    ${inputType === 'password' ? '<button type="button" class="toggle-vis" title="Toggle visibility">Show</button>' : ''}
                </div>
                ${helpHtml}
            </label>
        `;
    }

    card.innerHTML = `
        <div class="card-header">
            <div>
                <h3>${integration.name.replace(/_/g, ' ')}</h3>
                <span class="card-category">${integration.category}</span>
            </div>
            <span class="card-status ${statusClass}">${statusText}</span>
        </div>
        <div class="card-fields">${fieldsHtml}</div>
        <div class="card-actions">
            <button class="settings-btn primary" data-action="save">Save</button>
            <button class="settings-btn" data-action="test">Test Connection</button>
            <button class="settings-btn danger" data-action="clear">Clear</button>
        </div>
        <div class="card-message" style="display:none;"></div>
    `;

    // Toggle password visibility
    card.querySelectorAll('.toggle-vis').forEach(btn => {
        btn.addEventListener('click', () => {
            const input = btn.parentElement.querySelector('input');
            if (input.type === 'password') {
                input.type = 'text';
                btn.textContent = 'Hide';
            } else {
                input.type = 'password';
                btn.textContent = 'Show';
            }
        });
    });

    // Save
    card.querySelector('[data-action="save"]').addEventListener('click', async () => {
        const body = {};
        card.querySelectorAll('[data-field]').forEach(input => {
            if (input.value) body[input.name] = input.value;
        });
        await apiCall('PUT', `/api/integrations/${integration.name}/credentials`, body, card);
    });

    // Test
    card.querySelector('[data-action="test"]').addEventListener('click', async () => {
        await apiCall('GET', `/api/integrations/${integration.name}/status`, null, card);
    });

    // Clear
    card.querySelector('[data-action="clear"]').addEventListener('click', async () => {
        if (!confirm('Clear all credentials for this integration?')) return;
        await apiCall('DELETE', `/api/integrations/${integration.name}/credentials`, null, card);
    });

    cardsContainer.appendChild(card);
}

async function apiCall(method, url, body, card) {
    const msgEl = card.querySelector('.card-message');
    msgEl.style.display = 'block';
    msgEl.className = 'card-message loading';
    msgEl.textContent = 'Working...';

    try {
        const opts = { method };
        if (body) {
            opts.headers = { 'Content-Type': 'application/json' };
            opts.body = JSON.stringify(body);
        }
        const resp = await fetch(url, opts);
        const data = await resp.json();

        if (!resp.ok) {
            msgEl.className = 'card-message error';
            msgEl.textContent = data.detail || 'Request failed';
            return;
        }

        if (data.healthy !== undefined) {
            msgEl.className = data.healthy ? 'card-message success' : 'card-message error';
            msgEl.textContent = data.healthy ? 'Connection successful' : 'Connection failed';
        } else {
            msgEl.className = 'card-message success';
            msgEl.textContent = 'Done';
            // Reload to update configured status
            setTimeout(loadIntegrations, 800);
        }
    } catch (err) {
        msgEl.className = 'card-message error';
        msgEl.textContent = 'Network error';
        console.error(err);
    }
}

loadIntegrations();
```

- [ ] **Step 3: Add settings page styles to style.css**

Append to `web/style.css`:

```css
/* --- Settings Page --- */

.settings-page main {
    overflow-y: auto;
    padding: 1.5rem;
}

.settings-section-title {
    font-family: var(--font-display);
    font-size: 1.3rem;
    font-weight: 500;
    color: var(--brass);
    letter-spacing: 0.02em;
    margin-bottom: 0.3rem;
}

.settings-desc {
    font-size: 0.85rem;
    color: var(--text-dim);
    margin-bottom: 1.5rem;
}

.settings-error {
    color: var(--accent-red);
    font-style: italic;
}

.back-link {
    color: var(--text-secondary);
    text-decoration: none;
    display: flex;
    align-items: center;
    transition: color 0.2s;
}

.back-link:hover {
    color: var(--text-primary);
}

.integration-card {
    background: var(--bg-surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 1.25rem;
    margin-bottom: 1rem;
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 1rem;
}

.card-header h3 {
    font-family: var(--font-display);
    font-size: 1.1rem;
    font-weight: 500;
    color: var(--text-primary);
    text-transform: capitalize;
}

.card-category {
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-dim);
}

.card-status {
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 0.2rem 0.5rem;
    border-radius: 2px;
}

.card-status.configured {
    color: var(--accent-green);
    border: 1px solid var(--accent-green);
}

.card-status.unconfigured {
    color: var(--text-dim);
    border: 1px solid var(--border);
}

.card-fields {
    display: flex;
    flex-direction: column;
    gap: 0.8rem;
    margin-bottom: 1rem;
}

.settings-label {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    font-size: 0.9rem;
    color: var(--text-secondary);
}

.label-text {
    display: flex;
    align-items: center;
    gap: 0.5rem;
}

.field-badge {
    font-size: 0.65rem;
    color: var(--text-dim);
    border: 1px solid var(--border);
    padding: 0.05rem 0.35rem;
    border-radius: 2px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
}

.field-help {
    font-size: 0.78rem;
    color: var(--text-dim);
    font-style: italic;
}

.input-wrapper {
    display: flex;
    gap: 0.4rem;
}

.input-wrapper input {
    flex: 1;
    background: var(--bg-deep);
    border: 1px solid var(--border);
    border-radius: 3px;
    padding: 0.5rem 0.7rem;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 0.85rem;
}

.input-wrapper input:focus {
    outline: none;
    border-color: var(--brass-dim);
}

.toggle-vis {
    background: var(--bg-raised);
    border: 1px solid var(--border);
    border-radius: 3px;
    color: var(--text-dim);
    font-family: var(--font-body);
    font-size: 0.75rem;
    padding: 0 0.5rem;
    cursor: pointer;
    white-space: nowrap;
}

.toggle-vis:hover {
    color: var(--text-secondary);
    border-color: var(--border-light);
}

.card-actions {
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
}

.settings-btn {
    padding: 0.4rem 1rem;
    font-family: var(--font-body);
    font-size: 0.85rem;
    background: var(--bg-raised);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    border-radius: 3px;
    cursor: pointer;
    transition: all 0.2s;
}

.settings-btn:hover {
    color: var(--text-primary);
    border-color: var(--border-light);
}

.settings-btn.primary {
    background: var(--brass-dim);
    color: var(--bg-deep);
    border-color: var(--brass-dim);
}

.settings-btn.primary:hover {
    background: var(--brass);
}

.settings-btn.danger {
    color: var(--accent-red);
}

.settings-btn.danger:hover {
    border-color: var(--accent-red);
}

.card-message {
    margin-top: 0.6rem;
    font-size: 0.8rem;
    padding: 0.35rem 0.6rem;
    border-radius: 2px;
}

.card-message.loading {
    color: var(--text-dim);
}

.card-message.success {
    color: var(--accent-green);
    background: rgba(90, 122, 74, 0.1);
}

.card-message.error {
    color: var(--accent-red);
    background: rgba(138, 74, 74, 0.1);
}

/* Settings gear icon */
.gear-link {
    color: var(--text-dim);
    text-decoration: none;
    display: flex;
    align-items: center;
    transition: color 0.2s;
}

.gear-link:hover {
    color: var(--text-secondary);
}

/* Onboarding skip button */
.onboarding-btn.skip {
    background: transparent;
    color: var(--text-dim);
    border: none;
    font-size: 0.85rem;
    text-decoration: underline;
    text-underline-offset: 2px;
}

.onboarding-btn.skip:hover {
    color: var(--text-secondary);
}

/* Onboarding integration fields */
.onboarding-integration {
    margin-bottom: 1rem;
}

.onboarding-integration h3 {
    font-family: var(--font-display);
    font-size: 1rem;
    font-weight: 500;
    color: var(--text-primary);
    text-transform: capitalize;
    margin-bottom: 0.5rem;
}
```

- [ ] **Step 4: Add gear icon to index.html header**

Edit `web/index.html`. In the `<div class="header-meta">` section, add a gear icon link before the status span:

```html
                <div class="header-meta">
                    <a href="/settings.html" class="gear-link" title="Settings">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                            <circle cx="12" cy="12" r="3"/>
                            <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06A1.65 1.65 0 0 0 19.4 9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/>
                        </svg>
                    </a>
                    <span id="status" class="status">
```

- [ ] **Step 5: Test manually**

Run the web server and verify:
1. Gear icon appears in header and links to `/settings.html`
2. Settings page loads and shows integration cards
3. Saving credentials works (check via keyring)
4. Test Connection shows health status
5. Clear removes credentials
6. Back arrow returns to chat

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && uv run uvicorn core.channels.web_server:app --factory --port 8081` (visit `http://localhost:8081`)

- [ ] **Step 6: Commit**

```bash
git add web/settings.html web/settings.js web/style.css web/index.html
git commit -m "feat: add integration settings page with credential management UI"
```

---

## Task 9: Onboarding — Integrations Step + Skip Buttons

**Files:**
- Modify: `web/index.html`
- Modify: `web/app.js`

- [ ] **Step 1: Add skip buttons and integrations step to index.html**

Edit `web/index.html`. Changes:

1. Add a skip button to each onboarding step (steps 0-4). In each step's navigation area, add:
```html
<button class="onboarding-btn skip" data-skip>Skip — use defaults</button>
```

2. Add the integrations step (step 4) before the completion step. The completion step becomes step 5:

```html
            <!-- Step 4: Integrations -->
            <div class="onboarding-step" data-step="4" style="display:none;">
                <h2>Connections</h2>
                <p class="onboarding-body">
                    Connect your services so I can provide more informed assistance.
                    You can always configure these later in Settings.
                </p>
                <div id="ob-integrations"></div>
                <div class="onboarding-nav">
                    <button class="onboarding-btn secondary" data-next="3">Back</button>
                    <button class="onboarding-btn skip" data-skip>Skip — use defaults</button>
                    <button class="onboarding-btn" data-next="5">Continue</button>
                </div>
            </div>
```

3. Update the completion step from `data-step="4"` to `data-step="5"`.

4. Add a 6th progress dot:
```html
                <span class="progress-dot" data-dot="5"></span>
```

5. Update `data-next` on step 3's Continue button from `data-next="4"` to `data-next="4"` (it stays 4, since we inserted before completion).

6. Update step 3's Continue button: the current completion screen was step 4, now it's step 5. But step 3 already had `data-next` pointing to what's now step 4 (integrations), so just verify the numbering is correct.

- [ ] **Step 2: Update app.js onboarding flow**

Edit `web/app.js`. Changes:

1. Add skip button handler in `initOnboarding()`:

```javascript
    // Skip buttons — submit defaults and jump to completion
    overlay.querySelectorAll('[data-skip]').forEach(btn => {
        btn.addEventListener('click', async () => {
            // Submit onboarding with defaults (nulls → backend fills defaults)
            try {
                await fetch('/api/onboarding', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({}),
                });
            } catch (err) {
                console.error('Onboarding default save failed:', err);
            }
            localStorage.setItem('alfred_onboarded', '1');
            showStep(5);  // completion screen
        });
    });
```

2. **Restructure `ob-finish` button placement.** The key changes to `index.html`:

   - **Step 3 (Guest):** Replace `id="ob-finish"` button with a regular `data-next="4"` Continue button. Add skip button.
   - **Step 4 (Integrations):** The Continue button gets `id="ob-finish"` — this is where preferences + credentials are saved.
   - **Step 5 (Completion):** Was step 4. Update `data-step` from `"4"` to `"5"`.

   Step 3 navigation becomes:
   ```html
                   <div class="onboarding-nav">
                       <button class="onboarding-btn secondary" data-next="2">Back</button>
                       <button class="onboarding-btn skip" data-skip>Skip — use defaults</button>
                       <button class="onboarding-btn" data-next="4">Continue</button>
                   </div>
   ```

   Step 4 (integrations) Continue button:
   ```html
                       <button class="onboarding-btn" id="ob-finish">Continue</button>
   ```

3. **Replace the `ob-finish` handler** in `app.js` to save preferences AND integration credentials:

```javascript
    document.getElementById('ob-finish').addEventListener('click', async () => {
        const payload = {
            wake_time: document.getElementById('ob-wake-time').value || null,
            work_address: document.getElementById('ob-work-address').value || null,
            dietary_restrictions: document.getElementById('ob-dietary').value || null,
            proactivity_level: document.querySelector('input[name="proactivity"]:checked')?.value || 'moderate',
            guest_controls: Array.from(overlay.querySelectorAll('.onboarding-checks input:checked')).map(cb => cb.value),
        };

        // Save preferences
        try {
            await fetch('/api/onboarding', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
        } catch (err) {
            console.error('Onboarding save failed:', err);
        }

        // Save integration credentials (if any filled in)
        const integrationCards = document.querySelectorAll('#ob-integrations .onboarding-integration');
        for (const card of integrationCards) {
            const name = card.dataset.integration;
            const body = {};
            card.querySelectorAll('[data-field]').forEach(input => {
                if (input.value) body[input.name] = input.value;
            });
            if (Object.keys(body).length > 0) {
                try {
                    await fetch(`/api/integrations/${name}/credentials`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body),
                    });
                } catch (err) {
                    console.error(`Failed to save ${name} credentials:`, err);
                }
            }
        }

        localStorage.setItem('alfred_onboarded', '1');
        showStep(5);
    });
```

4. Add integration card rendering in `initOnboarding()`:

```javascript
    // Load integration schemas for onboarding step 4
    fetch('/api/integrations')
        .then(r => r.json())
        .then(integrations => {
            const container = document.getElementById('ob-integrations');
            integrations.forEach(integration => {
                if (!Object.keys(integration.schema.fields).length) return;
                const div = document.createElement('div');
                div.className = 'onboarding-integration';
                div.dataset.integration = integration.name;

                let fieldsHtml = '';
                for (const [name, field] of Object.entries(integration.schema.fields)) {
                    const inputType = field.field_type === 'password' ? 'password' : 'text';
                    fieldsHtml += `
                        <label class="onboarding-label">
                            ${field.label}
                            <input type="${inputType}" name="${name}" data-field="${name}"
                                   placeholder="${field.placeholder || ''}" autocomplete="off">
                        </label>
                    `;
                }
                div.innerHTML = `<h3>${integration.name.replace(/_/g, ' ')}</h3>${fieldsHtml}`;
                container.appendChild(div);
            });
        })
        .catch(err => console.error('Failed to load integrations for onboarding:', err));
```

5. Update `ob-close` to reference step 5:

The `ob-close` button is already on the completion step. Just make sure it's on `data-step="5"`.

- [ ] **Step 3: Test manually**

Run web server and verify:
1. Onboarding shows 6 progress dots
2. Skip button on any step submits defaults and jumps to completion
3. Integrations step shows credential fields for adapters with schemas
4. Filling in credentials and clicking Continue saves them
5. Skipping integrations step works correctly
6. `localStorage.alfred_onboarded` is set correctly

- [ ] **Step 4: Commit**

```bash
git add web/index.html web/app.js
git commit -m "feat: add integrations onboarding step and skip buttons"
```

---

## Task 10: Documentation

**Files:**
- Create: `docs/secrets.md`
- Modify: `docs/backlog/remaining-work.md`

- [ ] **Step 1: Create docs/secrets.md**

```markdown
# Secrets Manager

Secure credential storage for integration adapters using OS-native keychains.

## Architecture

```mermaid
graph TD
    UI[Settings Page / Onboarding] -->|PUT /api/integrations/{name}/credentials| API[REST Endpoints]
    API -->|aset_secret| Secrets[shared/secrets.py]
    Secrets -->|keyring.set_password| Keyring[OS Keychain]
    API -->|reconfigure| Registry[IntegrationRegistry]
    Registry -->|get_all_secrets| Secrets
    Registry -->|instantiate| Adapters[Integration Adapters]
    Adapters -->|credentials_schema| Schema[CredentialSchema]
```

## How It Works

1. Each `Integration` adapter declares a `credentials_schema` (`CredentialSchema`) listing its credential fields with types, labels, and validation rules.
2. `shared/secrets.py` wraps the `keyring` library — all credentials stored under service name `"alfred"` with key format `"{integration}.{field}"`.
3. `IntegrationRegistry.get()` auto-populates adapter constructor kwargs from keyring when no explicit kwargs are provided.
4. REST endpoints on the web server provide CRUD operations restricted to localhost.
5. The frontend settings page and onboarding wizard render credential forms dynamically from the adapter schemas.

## Credential Fields

| Adapter | Fields | Notes |
|---------|--------|-------|
| `apple_calendar` | `caldav_url`, `username`, `password` | App-specific password required |
| `robinhood` | `username`, `password`, `mfa_code` (transient) | MFA not persisted |
| `weather` | none | Open-Meteo is keyless |
| `apple_health` | none | Local endpoint, no auth |

## Keyring Backends

| Environment | Backend | Notes |
|-------------|---------|-------|
| macOS (dev) | Keychain Access | Automatic |
| Linux (prod) | SecretService | GNOME Keyring / KDE Wallet |
| Containers | Host D-Bus mount | `-v /run/user/1000/bus:/run/user/1000/bus` + `DBUS_SESSION_BUS_ADDRESS` env var |
| Tests | In-memory mock | `keyring.set_keyring()` |

## API Endpoints

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/api/integrations` | none | List integrations with schema + configured status |
| PUT | `/api/integrations/{name}/credentials` | localhost | Save credentials to keyring |
| DELETE | `/api/integrations/{name}/credentials` | localhost | Clear credentials |
| GET | `/api/integrations/{name}/status` | none | Run health check |

## Security

- Credential values are never returned in GET responses — only boolean configured status
- PUT/DELETE endpoints restricted to localhost via `Depends(require_localhost)`
- Password fields are never pre-filled in the UI
- Transient fields (e.g. MFA codes) are passed to the adapter but not persisted
```

- [ ] **Step 2: Mark D25 as DONE in backlog**

Edit `docs/backlog/remaining-work.md`. Change the D25 row:

From:
```
| D25 | Secrets manager for integration credentials | Section 15 | CalDAV password, Robinhood credentials, etc. stored in plaintext .env. Use `keyring` library ...
```

To:
```
| ~~D25~~ | ~~Secrets manager for integration credentials~~ | ~~Section 15~~ | DONE — keyring-based credential storage with self-describing CredentialSchema, REST API, settings UI, onboarding integration step |
```

- [ ] **Step 3: Commit**

```bash
git add docs/secrets.md docs/backlog/remaining-work.md
git commit -m "docs: add secrets manager architecture doc, mark D25 as done"
```

---

## Task 11: Lint, Type Check, Full Test Suite

**Files:** All changed files

- [ ] **Step 1: Run ruff**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && ruff check . --fix && ruff format .`
Expected: no errors after auto-fix

- [ ] **Step 2: Run mypy on all changed packages**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && mypy --strict shared/ core/integrations/ core/channels/`
Expected: PASS

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/anirudhlath/code/private/alfred/alfred && python -m pytest -v`
Expected: all tests PASS

- [ ] **Step 4: Fix any issues and commit**

```bash
git add -A
git commit -m "chore: lint + type fixes for secrets manager"
```

---

## Task 12: Simplify (Code Review)

- [ ] **Step 1: Invoke the simplify skill**

Use `superpowers:requesting-code-review` to review all changes made in this plan.

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "refactor: simplify secrets manager per code review"
```

---

## Task 13: Code Architect Review

- [ ] **Step 1: Dispatch code-architect review agent**

Use `feature-dev:code-architect` to review all changes against the spec and Five Pillars.

- [ ] **Step 2: Fix any issues found**

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "fix: address code architect review feedback"
```
