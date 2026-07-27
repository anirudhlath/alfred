"""Keyring-based secrets store for integration credentials.

Wraps the `keyring` library to provide sync and async access to OS-native
credential storage (macOS Keychain, Linux SecretService).

Sync API is used by IntegrationRegistry.get() at startup.
Async API (a-prefixed) is used by REST endpoints to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import configparser
import contextlib
import fcntl
import functools
import io
import os
import secrets as _secrets
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import keyring
from keyring.errors import PasswordDeleteError

from shared.fs import atomic_write

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVICE = "alfred"

# Guards every access to the cryptfile keyring. Held across the backend's whole
# read-modify-write, so concurrent writers can't interleave (see _keyring_lock).
_LOCK_SUFFIX = ".lock"
_process_lock = threading.RLock()
_lock_state = threading.local()


@contextmanager
def _keyring_lock(path: Path) -> Iterator[None]:
    """Serialize keyring file access across every Alfred process and thread.

    ``CryptFileKeyring`` read-modify-writes a plaintext-structured ``.cfg`` on each
    set/delete. Alfred runs nine processes against one shared keyring file, so two
    concurrent writes interleave and append a *duplicate* section — which configparser
    then refuses to parse. That is fatal rather than cosmetic: the backend is configured
    at import time, so a corrupt file stops every process from starting.

    Always exclusive, never shared: the backend's read paths can themselves rewrite the
    file (``_check_file`` → ``_migrate``), so a shared lock would need upgrading mid-call.

    Re-entrant by necessity — the backend nests its own calls (``_init_file`` →
    ``set_password``, ``_unlock`` → ``get_password``) and ``flock`` on a second
    descriptor from the same process would deadlock. Nested acquisitions ride the
    outer lock instead of taking a new one.
    """
    depth: int = getattr(_lock_state, "depth", 0)
    if depth:
        _lock_state.depth = depth + 1
        try:
            yield
        finally:
            _lock_state.depth -= 1
        return

    lock_path = path.with_name(path.name + _LOCK_SUFFIX)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with _process_lock:
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _lock_state.depth = 1
            yield
        finally:
            _lock_state.depth = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def repair_keyring_file(path: Path) -> bool:
    """Merge duplicate sections in a keyring file corrupted by concurrent writes.

    Returns True if a repair was performed. Files written before locking existed can
    contain two ``[alfred]`` sections; configparser's strict parser rejects those, and
    because the backend is built at import time that would keep every process from
    starting. Re-reading non-strict merges the duplicates (later values win — the most
    recent write of each credential is the one kept) and the file is rewritten atomically.
    """
    if not path.is_file():
        return False
    strict = configparser.RawConfigParser()
    try:
        strict.read(path)
    except configparser.Error:
        pass  # corrupt — fall through and attempt the merge
    else:
        return False  # parses cleanly, nothing to repair

    from loguru import logger

    merged = configparser.RawConfigParser(strict=False)
    try:
        merged.read(path)
    except configparser.Error as exc:
        # Damaged beyond a duplicate-section merge. Don't raise: a broken secrets file
        # should cost you the integrations that need it, not the whole system.
        logger.error(
            "Keyring file {} is unreadable ({}). Stored credentials are unavailable; "
            "move it aside and re-enter them to recover.",
            path,
            exc,
        )
        return False

    buffer = io.StringIO()
    merged.write(buffer)
    atomic_write(path, buffer.getvalue())
    logger.warning(
        "Repaired keyring file {} — merged duplicate sections left by concurrent writes.",
        path,
    )
    return True


@functools.cache
def _locked_keyring_cls() -> type[Any]:
    """Build the locking CryptFileKeyring subclass (import stays lazy for macOS/native)."""
    from keyrings.cryptfile.cryptfile import CryptFileKeyring

    class LockedCryptFileKeyring(CryptFileKeyring):  # type: ignore[misc]
        """CryptFileKeyring whose file access is serialized across processes."""

        def get_password(self, service: str, username: str) -> str | None:
            with _keyring_lock(Path(self.file_path)):
                result: str | None = super().get_password(service, username)
                return result

        def set_password(self, service: str, username: str, password: str) -> None:
            with _keyring_lock(Path(self.file_path)):
                super().set_password(service, username, password)

        def delete_password(self, service: str, username: str) -> None:
            with _keyring_lock(Path(self.file_path)):
                super().delete_password(service, username)

    return LockedCryptFileKeyring


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
    from shared.config import data_path

    secrets_dir = data_path("secrets")
    secrets_dir.mkdir(parents=True, exist_ok=True)
    passphrase = _resolve_passphrase(secrets_dir)
    keyring_path = secrets_dir / "keyring.cfg"
    # Heal a file damaged by pre-lock concurrent writes before anything reads it,
    # so an old corruption can't keep this process from starting.
    with _keyring_lock(keyring_path):
        repair_keyring_file(keyring_path)
    kr = _locked_keyring_cls()()
    kr.file_path = str(keyring_path)
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
