"""`.env.example` is a working config, not just documentation.

`cp .env.example .env` is the documented first step, and both launchers pass that file
straight through as the container's environment — so every key it ships blank ("leave
blank to accept the default", `.env.example:3`) reaches `AlfredConfig.from_env()` as
`""`, not as absent. That defeats `os.getenv`'s own default and any fallback chain built
on it, and the parsed keys then raise at import in every service at once. These tests
are the guard: the shipped file must load, and shipping a key blank must mean the same
thing as leaving it out.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path  # noqa: TC003

import pytest  # noqa: TC002
from dotenv import dotenv_values

from shared.config import AlfredConfig


def _example_path() -> Path:
    from alfredctl import staging

    path = staging.repo_root() / ".env.example"
    # dotenv_values on a missing path returns {} without complaint, which would make
    # every assertion below pass vacuously if the file were moved or renamed.
    assert path.is_file(), f"{path} is missing"
    return path


def _example_env() -> dict[str, str]:
    return {k: v for k, v in dotenv_values(_example_path()).items() if v is not None}


def test_env_example_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _example_env().items():
        monkeypatch.setenv(key, value)
    AlfredConfig.from_env()


def test_blank_keys_mean_the_same_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The promise `.env.example:3` makes, checked against every key it ships blank.

    Field by field would only ever cover the block whoever wrote the test was thinking
    about; the failure mode is generic, so the check is too.
    """
    example = _example_env()
    for key, value in example.items():
        monkeypatch.setenv(key, value)
    shipped = AlfredConfig.from_env()

    offenders: dict[str, list[str]] = {}
    for key in sorted(k for k, v in example.items() if not v.strip()):
        monkeypatch.delenv(key)
        absent = AlfredConfig.from_env()
        monkeypatch.setenv(key, example[key])
        differing = [
            f"{f.name}: blank={getattr(shipped, f.name)!r} absent={getattr(absent, f.name)!r}"
            for f in fields(AlfredConfig)
            if getattr(shipped, f.name) != getattr(absent, f.name)
        ]
        if differing:
            offenders[key] = differing
    assert not offenders, f"blank in .env.example is not the default: {offenders}"


def test_env_example_documents_every_embedding_key() -> None:
    # Derived, not restated: EMBEDDING_* fields are named after their env var, so a new
    # knob that never reaches .env.example fails here without anyone updating the test.
    expected = {f.name.upper() for f in fields(AlfredConfig) if f.name.startswith("embedding_")}
    assert expected, "no embedding fields found — the naming convention changed"
    assert expected <= set(_example_env())
