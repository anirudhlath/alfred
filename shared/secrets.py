"""Keyring-based secrets store for integration credentials.

Wraps the `keyring` library to provide sync and async access to OS-native
credential storage (macOS Keychain, Linux SecretService).

Sync API is used by IntegrationRegistry.get() at startup.
Async API (a-prefixed) is used by REST endpoints to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys

import keyring
from keyring.errors import PasswordDeleteError

SERVICE = "alfred"


def select_backend_name() -> str:
    """Choose the keyring backend: 'native' (macOS default) or 'cryptfile' (container/Linux)."""
    explicit = os.getenv("ALFRED_SECRETS_BACKEND", "").strip().lower()
    if explicit in ("cryptfile", "native"):
        return explicit
    return "native" if sys.platform == "darwin" else "cryptfile"


def configure_backend() -> None:
    """Configure the active keyring backend based on select_backend_name()."""
    if select_backend_name() != "cryptfile":
        return  # leave keyring's auto-detected native backend in place
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    from shared.config import data_path

    explicit = os.getenv("ALFRED_SECRETS_BACKEND", "").strip().lower() == "cryptfile"
    passphrase = os.getenv("ALFRED_SECRETS_PASSPHRASE", "")
    if not passphrase:
        if explicit:
            raise RuntimeError(
                "ALFRED_SECRETS_BACKEND=cryptfile requires ALFRED_SECRETS_PASSPHRASE. "
                "Set it in the environment (alfredctl generates and persists one for you)."
            )
        # Auto-detected on a bare Linux host (CI, devcontainer): stay importable, but
        # credentials stored this way are only obfuscated, not protected.
        from loguru import logger

        logger.warning(
            "cryptfile keyring auto-selected without ALFRED_SECRETS_PASSPHRASE — "
            "using an INSECURE default key; do not store real credentials"
        )
        passphrase = "alfred-insecure-default"

    secrets_dir = data_path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    kr = CryptFileKeyring()
    kr.file_path = str(secrets_dir / "keyring.cfg")
    kr.keyring_key = passphrase
    keyring.set_keyring(kr)


configure_backend()


# --- Sync API ---


def get_secret(integration: str, field: str) -> str | None:
    """Retrieve a credential field from the OS keyring. Returns None if not set."""
    return keyring.get_password(SERVICE, f"{integration}.{field}")


def set_secret(integration: str, field: str, value: str) -> None:
    """Store a credential field in the OS keyring."""
    keyring.set_password(SERVICE, f"{integration}.{field}", value)


def delete_secret(integration: str, field: str) -> None:
    """Remove a credential field from the OS keyring. No-op if not found."""
    with contextlib.suppress(PasswordDeleteError):
        keyring.delete_password(SERVICE, f"{integration}.{field}")


def get_all_secrets(integration: str, fields: list[str]) -> dict[str, str]:
    """Fetch all credential fields for an integration. Returns only non-None values."""
    return {f: v for f in fields if (v := get_secret(integration, f)) is not None}


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
