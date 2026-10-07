from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from alfred_cli.main import app

if TYPE_CHECKING:
    import pytest

runner = CliRunner()


def test_alfred_has_an_evals_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "evals" in result.output


def test_evals_memory_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[list[str]] = []
    monkeypatch.setattr("evals.__main__.main", lambda argv: seen.append(list(argv)))
    result = runner.invoke(app, ["evals", "memory", "runs", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert seen == [["memory", "runs", "--limit", "3"]]
