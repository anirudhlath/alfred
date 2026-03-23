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
