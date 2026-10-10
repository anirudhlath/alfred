from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def read_credentials(data_dir: Path) -> dict[str, str | None]:
    """Read the actual container keyring in a fresh interpreter, outside pytest's fake."""
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from shared.secrets import get_secret; "
            "import json; print(json.dumps({field: get_secret('apple_calendar', field) "
            "for field in ('username', 'password', 'caldav_url')}))",
        ],
        env={
            **os.environ,
            "ALFRED_DATA_DIR": str(data_dir),
            "ALFRED_SECRETS_BACKEND": "cryptfile",
            "ALFRED_SECRETS_PASSPHRASE": "alfred-eval-not-a-secret",
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(out.stdout)  # type: ignore[no-any-return]


async def test_prepares_container_keyring_without_touching_host_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import keyring

    from evals.harness.credentials import prepare_credentials

    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    fixture = unrelated / "keyring.cfg"
    fixture.write_bytes(b"unrelated keyring fixture\n")
    backend = keyring.get_keyring()
    monkeypatch.setenv("ALFRED_DATA_DIR", str(unrelated))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "native")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "unrelated passphrase")
    data = tmp_path / "new-eval-data"
    data.mkdir()
    fake = {
        "username": "eval@example.invalid",
        "password": 'fake-"key"\n$(echo unsafe)',
        "caldav_url": "http://127.0.0.1:9/eval",
    }
    await prepare_credentials(data, {"apple_calendar": fake})
    assert read_credentials(data) == fake
    assert fixture.read_bytes() == b"unrelated keyring fixture\n"
    assert keyring.get_keyring() is backend
    encrypted = (data / "secrets" / "keyring.cfg").read_text()
    assert fake["username"] not in encrypted and fake["password"] not in encrypted


async def test_empty_credentials_do_not_initialize_a_keyring(tmp_path: Path) -> None:
    from evals.harness.credentials import prepare_credentials

    await prepare_credentials(tmp_path, {})
    assert list(tmp_path.iterdir()) == []
