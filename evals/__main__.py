"""``python -m evals memory …`` — the memory-decay simulation (docs/evals-memory.md).

The PRD suites run with ``alfred evals`` (docs/evals.md).
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from evals.memory.cli import add_memory_parser, run_memory_command

if TYPE_CHECKING:
    from collections.abc import Sequence


def build_parser(prog: str = "python -m evals") -> argparse.ArgumentParser:
    """``prog`` is the command name the usage lines show (``alfred evals`` passes its own)."""
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Alfred's memory-decay eval. The PRD suites run with `alfred evals`.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    add_memory_parser(sub)
    return parser


def main(argv: Sequence[str] | None = None, prog: str = "python -m evals") -> None:
    args = build_parser(prog).parse_args(argv)
    run_memory_command(args)


if __name__ == "__main__":
    main()
