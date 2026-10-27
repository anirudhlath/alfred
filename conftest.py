"""Root conftest — shared fixtures for the entire monorepo."""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

# shared/secrets.py builds the cryptfile keyring backend at import time under
# data_path("secrets"), so merely importing a test module writes a real keyring
# into the checkout's ./data/. Point runtime state at a throwaway directory: the
# suite then never touches — or is broken by — a developer's real keyring, whose
# passphrase may not match whatever the current environment resolves to. This
# MUST run before the imports below, because that keyring is built at import
# time — not when a fixture runs.
if "ALFRED_DATA_DIR" not in os.environ:
    _tmp_data_dir = tempfile.mkdtemp(prefix="alfred-tests-")
    os.environ["ALFRED_DATA_DIR"] = _tmp_data_dir
    atexit.register(shutil.rmtree, _tmp_data_dir, ignore_errors=True)

from unittest.mock import AsyncMock

import keyring
import keyring.backend
import pydantic_ai as _pydantic_ai
import pytest
from pydantic_ai import models as _pydantic_ai_models

from bus.schemas.events import StateChangedEvent

# No test reaches a real model. A test that forgets its FunctionModel fails loudly here
# instead of calling the network.
_pydantic_ai_models.ALLOW_MODEL_REQUESTS = False
# Pydantic AI prints a banner on its first agent run; test output stays clean.
_pydantic_ai.BANNER_ENABLED = False


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

            raise PasswordDeleteError(username) from None


@pytest.fixture(autouse=True)
def _mock_keyring() -> None:
    """Install fresh in-memory keyring backend before each test."""
    keyring.set_keyring(InMemoryKeyring())


@pytest.fixture(autouse=True)
def _clear_telemetry() -> None:
    """Clear the telemetry buffer before and after each test."""
    from sdk.alfred_sdk.telemetry import clear_telemetry_buffer

    clear_telemetry_buffer()
    yield  # type: ignore[misc]
    clear_telemetry_buffer()


@pytest.fixture
def mock_embedder() -> AsyncMock:
    """Mock EmbeddingProvider returning deterministic 4-dim vectors."""
    embedder = AsyncMock()
    embedder.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    embedder.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
    embedder.dimension.return_value = 4
    embedder.model_name.return_value = "mock-model"
    return embedder


@pytest.fixture
def mock_vector_store() -> AsyncMock:
    """Mock VectorStore returning empty search results."""
    store = AsyncMock()
    store.search = AsyncMock(return_value=[])
    store.add = AsyncMock()
    store.delete = AsyncMock()
    store.exists = AsyncMock(return_value=False)
    store.count = AsyncMock(return_value=0)
    return store


@pytest.fixture
def tv_on_event() -> StateChangedEvent:
    """A TV turning on — the canonical test event."""
    return StateChangedEvent(
        source="home-service",
        domain="home",
        entity_id="media_player.living_room_tv",
        old_state="off",
        new_state="on",
        attributes={"friendly_name": "Living Room TV"},
    )
