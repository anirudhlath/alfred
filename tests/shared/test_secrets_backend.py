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


def test_explicit_cryptfile_with_passphrase_configures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "hunter2")
    secrets.configure_backend()  # must not raise


def test_cryptfile_without_passphrase_generates_and_persists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No passphrase → generate + persist a strong one (0600), not raise or use a default."""
    import stat

    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)

    secrets.configure_backend()  # must NOT raise

    marker = tmp_path / "secrets" / ".passphrase"
    assert marker.is_file()
    assert marker.read_text().strip()  # non-empty generated key
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    import keyring

    assert keyring.get_keyring().__class__.__name__ == "CryptFileKeyring"


def test_persisted_passphrase_is_reused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A generated passphrase is stable across runs (so the store stays decryptable)."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.delenv("ALFRED_SECRETS_PASSPHRASE", raising=False)

    secrets.configure_backend()
    marker = tmp_path / "secrets" / ".passphrase"
    first = marker.read_text()
    secrets.configure_backend()  # second boot
    assert marker.read_text() == first


def test_env_passphrase_wins_over_persisted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit ALFRED_SECRETS_PASSPHRASE takes precedence and isn't persisted."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "hunter2")

    assert secrets._resolve_passphrase(tmp_path / "secrets") == "hunter2"
    assert not (tmp_path / "secrets" / ".passphrase").exists()
