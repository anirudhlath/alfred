"""Secrets backend selection is env/platform driven."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest

from shared import secrets


def test_explicit_cryptfile_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    assert secrets.select_backend_name() == "cryptfile"


def test_native_selection_on_macos_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_BACKEND", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "darwin")
    assert secrets.select_backend_name() == "native"


def test_auto_cryptfile_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALFRED_SECRETS_BACKEND", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "linux")
    assert secrets.select_backend_name() == "cryptfile"


def test_configure_cryptfile_sets_keyring(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "test-pass")
    secrets.configure_backend()
    import keyring

    kr = keyring.get_keyring()
    assert kr.__class__.__name__ == "CryptFileKeyring"
    # round-trips through the encrypted file:
    secrets.set_secret("demo", "token", "sekret")
    assert secrets.get_secret("demo", "token") == "sekret"
    assert (tmp_path / "secrets").is_dir()


def test_explicit_cryptfile_without_passphrase_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)
    with pytest.raises(RuntimeError, match="ALFRED_SECRETS_PASSPHRASE"):
        secrets.configure_backend()


def test_explicit_cryptfile_with_passphrase_configures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "hunter2")
    secrets.configure_backend()  # must not raise


def test_auto_cryptfile_without_passphrase_warns_and_continues(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Auto-detected cryptfile (no explicit backend, no passphrase) warns but continues."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ALFRED_SECRETS_BACKEND", raising=False)
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)
    monkeypatch.setattr(secrets.sys, "platform", "linux")

    from loguru import logger

    messages: list[str] = []
    sink_id = logger.add(messages.append, level="WARNING")
    try:
        secrets.configure_backend()  # must not raise
    finally:
        logger.remove(sink_id)

    assert any("INSECURE" in str(m) for m in messages)
    import keyring

    kr = keyring.get_keyring()
    assert kr.__class__.__name__ == "CryptFileKeyring"


def test_explicit_cryptfile_empty_string_passphrase_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Explicit cryptfile backend with empty string passphrase must raise."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "")
    with pytest.raises(RuntimeError, match="ALFRED_SECRETS_PASSPHRASE"):
        secrets.configure_backend()
