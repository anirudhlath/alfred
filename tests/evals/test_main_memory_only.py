from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from evals.__main__ import build_parser, main
from evals.memory.policies import POLICIES

if TYPE_CHECKING:
    import pytest


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(action.choices)


def test_only_the_memory_subcommand_remains() -> None:
    assert _subcommands(build_parser()) == ["memory"]


def test_memory_subcommand_still_parses() -> None:
    args = build_parser().parse_args(["memory", "policies"])
    assert args.command == "memory"


def test_main_runs_the_memory_command(capsys: pytest.CaptureFixture[str]) -> None:
    main(["memory", "policies"])

    out = capsys.readouterr().out
    assert POLICIES, "the decay-policy registry is empty, so this test would prove nothing"
    for name in POLICIES:
        assert f"  {name}: " in out
