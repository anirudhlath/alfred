from __future__ import annotations

import argparse

from evals.__main__ import build_parser


def _subcommands(parser: argparse.ArgumentParser) -> list[str]:
    action = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return sorted(action.choices)


def test_only_the_memory_subcommand_remains() -> None:
    assert _subcommands(build_parser()) == ["memory"]


def test_memory_subcommand_still_parses() -> None:
    args = build_parser().parse_args(["memory", "policies"])
    assert args.command == "memory"
