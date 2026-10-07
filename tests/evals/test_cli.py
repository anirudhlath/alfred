from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from alfred_cli.main import app
from evals.__main__ import build_parser

if TYPE_CHECKING:
    from collections.abc import Sequence

    import pytest

runner = CliRunner()


def _record_memory_main(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], str]]:
    """Replace ``evals.__main__.main`` with a fake that records each ``(argv, prog)`` call."""
    seen: list[tuple[list[str], str]] = []

    def fake_main(argv: Sequence[str], prog: str = "python -m evals") -> None:
        seen.append((list(argv), prog))

    monkeypatch.setattr("evals.__main__.main", fake_main)
    return seen


def test_alfred_has_an_evals_group() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    assert "evals" in result.output


def test_evals_memory_passes_its_arguments_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_memory_main(monkeypatch)
    result = runner.invoke(app, ["evals", "memory", "runs", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert seen == [(["memory", "runs", "--limit", "3"], "alfred evals")]


def test_evals_memory_passes_help_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _record_memory_main(monkeypatch)
    result = runner.invoke(app, ["evals", "memory", "--help"])
    assert result.exit_code == 0, result.output
    assert seen == [(["memory", "--help"], "alfred evals")]


def test_parser_usage_names_the_given_prog() -> None:
    assert build_parser(prog="alfred evals").format_usage().startswith("usage: alfred evals")
