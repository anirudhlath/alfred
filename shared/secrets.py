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
import secrets as _secrets
import sys
from typing import TYPE_CHECKING

import keyring
from keyring.errors import PasswordDeleteError

if TYPE_CHECKING:
    from pathlib import Path

SERVICE = "alfred"


def select_backend_name() -> str:
    """Choose the keyring backend: 'native' (macOS default) or 'cryptfile' (container/Linux)."""
    explicit = os.getenv("ALFRED_SECRETS_BACKEND", "").strip().lower()
    if explicit in ("cryptfile", "native"):
        return explicit
    return "native" if sys.platform == "darwin" else "cryptfile"


def _resolve_passphrase(secrets_dir: Path) -> str:
    """Passphrase for the cryptfile keyring: env wins, else a persisted random one.

    ``ALFRED_SECRETS_PASSPHRASE`` always takes precedence. Otherwise a strong random
    passphrase is generated once and persisted (0600) under the data dir, so a plain
    ``docker compose up`` works with zero secret management. There is no insecure
    hardcoded fallback — the persisted key is real and lives with the encrypted store.
    """
    env_passphrase = os.getenv("ALFRED_SECRETS_PASSPHRASE", "").strip()
    if env_passphrase:
        return env_passphrase

    marker = secrets_dir / ".passphrase"
    if marker.is_file():
        return marker.read_text().strip()

    value = _secrets.token_urlsafe(32)
    fd = os.open(marker, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    try:
        os.write(fd, (value + "\n").encode())
    finally:
        os.close(fd)

    from loguru import logger

    logger.warning(
        "Generated a keyring passphrase at {} — it encrypts your stored credentials. "
        "Back up the data dir (or set ALFRED_SECRETS_PASSPHRASE) so credentials survive "
        "a volume loss.",
        marker,
    )
    return value


def configure_backend() -> None:
    """Configure the active keyring backend based on select_backend_name()."""
    if select_backend_name() != "cryptfile":
        return  # leave keyring's auto-detected native backend in place
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    from shared.config import data_path

    secrets_dir = data_path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    passphrase = _resolve_passphrase(secrets_dir)
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
