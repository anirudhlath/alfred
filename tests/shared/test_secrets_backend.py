"""Secrets backend selection is env/platform driven."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003

import pytest  # noqa: TC002

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
