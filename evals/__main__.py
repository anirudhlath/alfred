"""``python -m evals memory …`` — the memory-decay simulation (docs/evals-memory.md).

The PRD suites run with ``alfred evals`` (docs/evals.md).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from evals.memory.cli import add_memory_parser, run_memory_command

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="Alfred's memory-decay eval. The PRD suites run with `alfred evals`.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_memory_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_memory_command(args)


if __name__ == "__main__":
    main()
