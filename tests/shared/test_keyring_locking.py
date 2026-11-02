"""Concurrent keyring writes must not corrupt the cryptfile store.

Regression coverage for a production outage: two Alfred processes wrote the shared
cryptfile keyring at the same time, producing a file with two ``[alfred]`` sections.
configparser's strict parser then rejected it, and because the backend is configured at
*import* time that killed every service in a restart loop.
"""

from __future__ import annotations

import configparser
import subprocess
import sys
import textwrap
import threading
import time
import uuid
from pathlib import Path
from unittest.mock import Mock

import keyring
import keyring.backend
import pytest
from keyring.errors import KeyringError

from shared import secrets

_REPO_ROOT = Path(__file__).resolve().parents[2]

# A keyring file as produced by two interleaved writers: '[alfred]' appears twice.
_CORRUPT_KEYRING = """\
[alfred]
first_2ekey = value-one
scheme = PBKDF2AES256
version = 1.0

[alfred]
second_2ekey = value-two
first_2ekey = value-one-updated
"""


def _write(path: Path, body: str) -> Path:
    path.write_text(body)
    return path


def test_repair_merges_duplicate_sections(tmp_path: Path) -> None:
    path = _write(tmp_path / "keyring.cfg", _CORRUPT_KEYRING)

    assert secrets.repair_keyring_file(path) is True

    strict = configparser.RawConfigParser()
    strict.read(path)  # must no longer raise DuplicateSectionError
    assert strict.sections() == ["alfred"]
    # every key from both sections survives; the later write wins on conflicts
    assert strict["alfred"]["second_2ekey"] == "value-two"
    assert strict["alfred"]["first_2ekey"] == "value-one-updated"
    assert strict["alfred"]["scheme"] == "PBKDF2AES256"


def test_repair_is_noop_on_healthy_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "keyring.cfg", "[alfred]\nkey = value\n")
    before = path.read_text()

    assert secrets.repair_keyring_file(path) is False
    assert path.read_text() == before


def test_repair_missing_file_is_noop(tmp_path: Path) -> None:
    assert secrets.repair_keyring_file(tmp_path / "absent.cfg") is False


def test_unrepairable_file_does_not_raise(tmp_path: Path) -> None:
    """A mangled secrets file costs you the integrations, never the whole process."""
    path = _write(tmp_path / "keyring.cfg", "not an ini file\n=== garbage ===\n")

    assert secrets.repair_keyring_file(path) is False  # logged, not raised


def test_configure_backend_heals_corrupt_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The outage scenario: a corrupt keyring must not stop the process from starting."""
    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir(parents=True)
    _write(secrets_dir / "keyring.cfg", _CORRUPT_KEYRING)
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "test-pass")

    secrets.configure_backend()  # previously raised DuplicateSectionError

    strict = configparser.RawConfigParser()
    strict.read(secrets_dir / "keyring.cfg")  # strict parse == the corruption is gone
    sections = strict.sections()
    assert "alfred" in sections
    assert len(sections) == len(set(sections))  # no duplicates survived the merge
    assert strict["alfred"]["second_2ekey"] == "value-two"


def test_lock_is_reentrant(tmp_path: Path) -> None:
    """The backend nests its own calls (_init_file -> set_password); must not deadlock."""
    path = tmp_path / "keyring.cfg"
    with secrets._keyring_lock(path), secrets._keyring_lock(path):
        pass  # a non-reentrant flock would block here forever
    # depth unwinds fully, so a later acquisition still really locks
    assert getattr(secrets._lock_state, "depth", 0) == 0


def test_concurrent_threads_do_not_corrupt(tmp_path: Path) -> None:
    """Threads doing read-modify-write under the lock keep the file parseable."""
    path = tmp_path / "keyring.cfg"
    path.write_text("[alfred]\n")
    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            for round_ in range(3):
                with secrets._keyring_lock(path):
                    cp = configparser.RawConfigParser()
                    cp.read(path)
                    if not cp.has_section("alfred"):
                        cp.add_section("alfred")
                    cp["alfred"][f"key_{index}_{round_}"] = "v"
                    with path.open("w") as handle:
                        cp.write(handle)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors
    strict = configparser.RawConfigParser()
    strict.read(path)  # strict parse proves no duplicate sections were appended
    assert len(strict["alfred"]) == 12  # 4 writers x 3 rounds, none lost


# Runs in a separate interpreter so the lock is exercised across *processes* (flock),
# not just threads. ALFRED_SECRETS_BACKEND=native keeps importing shared.secrets cheap.
_WORKER = textwrap.dedent(
    """
    import configparser, os, sys, time
    from pathlib import Path

    sys.path.insert(0, sys.argv[1])
    os.environ["ALFRED_SECRETS_BACKEND"] = "native"
    from shared.secrets import _keyring_lock

    path, name = Path(sys.argv[2]), sys.argv[3]
    with _keyring_lock(path):
        cp = configparser.RawConfigParser()
        if path.exists():
            cp.read(path)
        cp.add_section(name)
        time.sleep(0.15)          # widen the window the real race exploited
        cp[name]["v"] = "1"
        with path.open("w") as handle:
            cp.write(handle)
    """
)


def test_concurrent_processes_do_not_corrupt(tmp_path: Path) -> None:
    """The actual outage shape: separate processes racing one keyring file."""
    path = tmp_path / "keyring.cfg"
    script = tmp_path / "worker.py"
    script.write_text(_WORKER)

    procs = [
        subprocess.Popen([sys.executable, str(script), str(_REPO_ROOT), str(path), f"sect{i}"])
        for i in range(3)
    ]
    for proc in procs:
        assert proc.wait(timeout=90) == 0

    strict = configparser.RawConfigParser()
    strict.read(path)  # unlocked, these interleave and this raises DuplicateSectionError
    assert sorted(strict.sections()) == ["sect0", "sect1", "sect2"]


def test_credentials_survive_concurrent_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End-to-end: real keyring, concurrent writers, every credential still readable."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "test-pass")
    secrets.configure_backend()

    errors: list[BaseException] = []

    def writer(index: int) -> None:
        try:
            secrets.set_secret("integration", f"key_{index}", f"secret_{index}")
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not errors
    for i in range(4):
        assert secrets.get_secret("integration", f"key_{i}") == f"secret_{i}"


# --- get_or_create_secret: one read-create-write under the keyring lock ---


def _slow_unique() -> str:
    """A create() that widens the read-to-write window the lock must close."""
    time.sleep(0.05)
    return uuid.uuid4().hex


def test_get_or_create_creates_once() -> None:
    create = Mock(side_effect=["first", "second"])
    assert secrets.get_or_create_secret("svc", "key", create) == ("first", True)
    assert secrets.get_or_create_secret("svc", "key", create) == ("first", False)
    assert create.call_count == 1
    assert secrets.get_secret("svc", "key") == "first"


def test_get_or_create_threads_agree() -> None:
    """Threads that all start with nothing stored get one value, created once."""
    results: list[tuple[str, bool]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(secrets.get_or_create_secret("svc", "key", _slow_unique))
        )
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(results) == 6
    assert len({value for value, _ in results}) == 1
    assert sum(created for _, created in results) == 1


def test_get_or_create_reenters_the_cryptfile_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """On the real backend the nested get/set ride the outer lock instead of deadlocking."""
    monkeypatch.setenv("ALFRED_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("ALFRED_SECRETS_BACKEND", "cryptfile")
    monkeypatch.setenv("ALFRED_SECRETS_PASSPHRASE", "test-pass")
    secrets.configure_backend()

    results: list[tuple[str, bool]] = []
    threads = [
        threading.Thread(
            target=lambda: results.append(secrets.get_or_create_secret("svc", "key", _slow_unique))
        )
        for _ in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert not any(t.is_alive() for t in threads)
    assert len({value for value, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    assert secrets.get_secret("svc", "key") == results[0][0]


_GET_OR_CREATE_WORKER = textwrap.dedent(
    """
    import os, sys, time, uuid

    sys.path.insert(0, sys.argv[1])
    os.environ["ALFRED_DATA_DIR"] = sys.argv[2]
    os.environ["ALFRED_SECRETS_BACKEND"] = "cryptfile"
    os.environ["ALFRED_SECRETS_PASSPHRASE"] = "test-pass"
    from shared.secrets import get_or_create_secret

    def create():
        time.sleep(0.15)          # widen the read-to-write window
        return uuid.uuid4().hex

    value, created = get_or_create_secret("svc", "key", create)
    print(value, created)
    """
)


def test_get_or_create_processes_agree(tmp_path: Path) -> None:
    """Separate processes racing a first create all get the one value that was stored."""
    script = tmp_path / "worker.py"
    script.write_text(_GET_OR_CREATE_WORKER)
    data_dir = tmp_path / "data"

    procs = [
        subprocess.Popen(
            [sys.executable, str(script), str(_REPO_ROOT), str(data_dir)],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outputs = [proc.communicate(timeout=90)[0].split() for proc in procs]
    assert all(proc.returncode == 0 for proc in procs)

    assert len({value for value, _ in outputs}) == 1
    assert [created for _, created in outputs].count("True") == 1


def test_get_or_create_never_creates_over_an_unreadable_keyring() -> None:
    """A read that raises propagates, and nothing is created or written (R5)."""

    class Unreadable(keyring.backend.KeyringBackend):
        priority = 1  # type: ignore[assignment]

        def __init__(self) -> None:
            self.writes: list[str] = []

        def get_password(self, service: str, username: str) -> str | None:
            raise KeyringError("locked")

        def set_password(self, service: str, username: str, password: str) -> None:
            self.writes.append(username)

        def delete_password(self, service: str, username: str) -> None:
            raise NotImplementedError

    backend = Unreadable()
    keyring.set_keyring(backend)
    create = Mock(return_value="new")

    with pytest.raises(KeyringError, match="locked"):
        secrets.get_or_create_secret("svc", "key", create)
    create.assert_not_called()
    assert backend.writes == []
    assert getattr(secrets._lock_state, "depth", 0) == 0  # the lock was released


async def test_aget_or_create_secret() -> None:
    assert await secrets.aget_or_create_secret("svc", "key", lambda: "v") == ("v", True)
    assert await secrets.aget_or_create_secret("svc", "key", lambda: "w") == ("v", False)
