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
from pathlib import Path

import pytest  # noqa: TC002

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
