"""Replay recorded Reflex decisions through a model, and count how they come out.

    python -m core.reflex.replay run --since 2026-10-26T23:35:00Z [--until ISO] [options]
    python -m core.reflex.replay run --records decisions.jsonl [options]

Each record's exact prompt goes to the model, one record at a time, through the same agent
and parser Reflex decides with (``core.reflex.agent``), with the tools the prompt showed
(``parse_tools``). Records come from the live decision log (``--since``, ``--until``) or from
a JSONL file with one ``DecisionRecord.model_dump_json()`` per line (``--records``).

The replay only reads. It writes nothing to Redis, publishes nothing and starts no tracing, so
nothing it asks reaches an observation, a counter or a span. What it prints is counts only: the
recorded-by-replayed table, agreement, the invalid rate and model latency p50 and p95. The
prompts name the house and its people, so neither they nor the reasons, entity ids or names in
them are ever printed. ``--out`` writes one line per record (its time and origin, the recorded
and replayed decision, the latency), in a file only its owner can read; keep it out of the repo.

A model server is not deterministic at temperature 0, so compare counts, never single records.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, NoReturn, TextIO, get_args

from bus.schemas.events import ReflexDecision
from core.reflex.agent import build_reflex_agent, decide
from core.reflex.decision_log import DecisionLog, DecisionRecord
from core.reflex.prompt import parse_tools
from shared.agents import resolve_model
from shared.config import AlfredConfig, apply_model_env
from shared.redis_streams import create_redis

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic_ai import Agent

    from bus.schemas.events import ReflexProposal
    from core.reflex.tool_registry import ToolInfo

# What a replayed call that raised (the server down, a timeout) is counted as.
ERROR: Final = "error"
# A server that fails this many calls in a row is down: stop rather than ask it a few hundred
# more times, each waiting out its timeout.
MAX_CONSECUTIVE_ERRORS: Final = 5
DECISIONS: Final[tuple[str, ...]] = get_args(ReflexDecision)
_COLUMNS: Final = (*DECISIONS, ERROR)
_PROG: Final = "python -m core.reflex.replay"


@dataclass(frozen=True)
class Outcome:
    """One record replayed: what was recorded, what the model decided now, how long it took."""

    time: datetime  # the record's
    origin: str
    recorded: str
    replayed: str  # a decision, or ERROR
    latency_ms: float | None  # None when the call raised
    error: str | None = None  # the exception's class name; never its message


async def _decide_one(
    agent: Agent[list[ToolInfo], ReflexProposal], record: DecisionRecord
) -> Outcome:
    recorded = record.proposal.decision
    started = time.perf_counter()
    try:
        proposal = await decide(agent, record.prompt, parse_tools(record.prompt))
    except Exception as exc:  # a transport or provider failure: counted, not fatal
        return Outcome(record.time, record.origin, recorded, ERROR, None, type(exc).__name__)
    latency_ms = (time.perf_counter() - started) * 1000
    return Outcome(record.time, record.origin, recorded, proposal.decision, latency_ms)


async def replay(
    records: Sequence[DecisionRecord],
    agent: Agent[list[ToolInfo], ReflexProposal],
    *,
    on_outcome: Callable[[Outcome], None] | None = None,
) -> list[Outcome]:
    """Decide each record again with *agent*, in order and one at a time.

    A call that raises is an ``error`` outcome and the run goes on; after
    ``MAX_CONSECUTIVE_ERRORS`` in a row the run stops, so the result is shorter than
    *records*. *on_outcome* sees each outcome as it is made.
    """
    outcomes: list[Outcome] = []
    streak = 0
    for record in records:
        outcome = await _decide_one(agent, record)
        outcomes.append(outcome)
        if on_outcome is not None:
            on_outcome(outcome)
        streak = streak + 1 if outcome.replayed == ERROR else 0
        if streak >= MAX_CONSECUTIVE_ERRORS:
            break
    return outcomes


def _share(part: int, whole: int, what: str = "") -> str:
    """``part of whole [what] (percent)``, for the calls that were answered."""
    if not whole:
        return "none answered"
    return f"{part} of {whole}{' ' + what if what else ''} ({100 * part / whole:.1f}%)"


def _percentile(sorted_values: Sequence[float], percent: int) -> float:
    """The nearest-rank percentile of an ascending, non-empty sequence."""
    rank = math.ceil(percent / 100 * len(sorted_values))
    return sorted_values[max(rank, 1) - 1]


def summarize(outcomes: Sequence[Outcome], *, total: int) -> str:
    """The counts: no prompt, entity, name or reason is in the outcomes, so none is printed.

    *total* is how many records the run meant to replay; a run that stopped early is shorter.
    """
    answered = [o for o in outcomes if o.replayed != ERROR]
    failed = Counter(o.error for o in outcomes if o.replayed == ERROR)
    lines = [f"Replayed {len(outcomes)} of {total} records, {sum(failed.values())} failed.", ""]

    cells = Counter((o.recorded, o.replayed) for o in outcomes)
    label = "recorded \\ replayed"
    width = max(len(label), *(len(d) for d in DECISIONS))
    lines.append(f"{label:<{width}}  " + "  ".join(f"{c:>7}" for c in _COLUMNS))
    for recorded in DECISIONS:
        counts = "  ".join(f"{cells[(recorded, c)]:>7}" for c in _COLUMNS)
        lines.append(f"{recorded:<{width}}  {counts}")
    lines.append("")

    agreed = sum(1 for o in answered if o.recorded == o.replayed)
    lines.append(f"Agreement: {_share(agreed, len(answered))}")
    replayed_invalid = sum(1 for o in answered if o.replayed == "invalid")
    recorded_invalid = sum(1 for o in answered if o.recorded == "invalid")
    lines.append(
        f"Invalid replies: {_share(replayed_invalid, len(answered), 'replayed')};"
        f" {_share(recorded_invalid, len(answered), 'recorded')}"
    )
    latencies = sorted(o.latency_ms for o in answered if o.latency_ms is not None)
    if latencies:
        p50, p95 = _percentile(latencies, 50), _percentile(latencies, 95)
        lines.append(f"Model latency: p50 {p50:.0f} ms, p95 {p95:.0f} ms")
    else:
        lines.append("Model latency: no call was answered")
    if failed:
        lines.append("Failed calls: " + ", ".join(f"{name} x{n}" for name, n in failed.items()))
    return "\n".join(lines)


@dataclass(frozen=True)
class Args:
    """The ``run`` command's options, checked: exactly one of ``since`` and ``records``."""

    since: datetime | None
    until: datetime | None
    records: Path | None
    limit: int | None
    model: str | None
    out: Path | None


def _moment(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not an ISO 8601 time") from None
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"{text!r} has no UTC offset; write it like 2026-10-26T23:35:00Z"
        )
    return moment


def _count(text: str) -> int:
    try:
        count = int(text)
    except ValueError:
        count = 0
    if count < 1:
        raise argparse.ArgumentTypeError(f"{text!r} is not a positive whole number")
    return count


def _parsers() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    parser = argparse.ArgumentParser(
        prog=_PROG, description="Replay recorded Reflex decisions through a model."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run",
        help="decide recorded prompts again and print counts",
        description=(
            "Decide recorded prompts again, one at a time, and print counts only. Give exactly"
            " one of --since (the live decision log) and --records (a JSONL file of"
            " DecisionRecord dumps). Nothing is written to Redis or published."
        ),
    )
    run.add_argument("--since", type=_moment, metavar="ISO", help="live log: from this time on")
    run.add_argument(
        "--until",
        type=_moment,
        metavar="ISO",
        help="live log, with --since: up to, not including, this time (default: now)",
    )
    run.add_argument(
        "--records", type=Path, metavar="FILE", help="JSONL: one DecisionRecord dump per line"
    )
    run.add_argument(
        "--limit",
        type=_count,
        metavar="N",
        help="replay at most N records, the oldest first (default: all)",
    )
    run.add_argument(
        "--model",
        metavar="MODEL",
        help="the model to decide with, e.g. vllm:<served name> (default: REFLEX_MODEL)",
    )
    run.add_argument(
        "--out",
        type=Path,
        metavar="FILE",
        help="write one JSONL line per record (private, mode 0600; keep it out of the repo)",
    )
    return parser, run


def parse_args(argv: Sequence[str] | None = None) -> Args:
    """The command line, or a usage error (exit status 2)."""
    parser, run = _parsers()
    ns = parser.parse_args(argv)
    if (ns.since is None) == (ns.records is None):
        run.error("give exactly one of --since (the live log) and --records (a JSONL file)")
    if ns.until is not None:
        if ns.since is None:
            run.error("--until goes with --since")
        if ns.until <= ns.since:
            run.error("--until must be after --since")
    if ns.records is not None and not ns.records.is_file():
        run.error(f"--records: {ns.records} is not a file")
    return Args(ns.since, ns.until, ns.records, ns.limit, ns.model, ns.out)


def _fail(message: str, code: int) -> NoReturn:
    print(f"{_PROG}: error: {message}", file=sys.stderr)
    raise SystemExit(code)


def build_agent(config: AlfredConfig, model: str | None) -> Agent[list[ToolInfo], ReflexProposal]:
    """The Reflex agent over *model*, or over ``REFLEX_MODEL`` when none is given.

    Exits non-zero when no model resolves; ``resolve_model`` logs why. Tracing is not
    started, so the agent's spans go nowhere: a replayed prompt is never exported.
    """
    apply_model_env(config)
    setting = "REFLEX_MODEL" if model is None else "--model"
    resolved = resolve_model(setting, config.reflex_model if model is None else model)
    if resolved is None:
        _fail("no model: set REFLEX_MODEL, or pass --model (vllm:<served name>)", 1)
    return build_reflex_agent(resolved)


def read_records_file(path: Path) -> list[DecisionRecord]:
    """Every record in a JSONL file; a bad line is a usage error naming its number only."""
    records: list[DecisionRecord] = []
    with path.open(encoding="utf-8") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(DecisionRecord.model_validate_json(line))
            except ValueError:  # the message would quote the line, prompt and all
                _fail(f"line {number} of {path} is not a decision record", 2)
    return records


async def _load(args: Args, config: AlfredConfig) -> list[DecisionRecord]:
    if args.records is not None:
        return read_records_file(args.records)
    if args.since is None:
        raise ValueError("neither --records nor --since")
    redis = create_redis(config.redis_url)
    try:
        return await DecisionLog(redis).read(since=args.since, until=args.until)
    finally:
        await redis.aclose()


def _open_private(path: Path) -> TextIO:
    """*path*, emptied and readable only by its owner, whether or not it existed."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    return os.fdopen(fd, "w", encoding="utf-8")


def _line(outcome: Outcome) -> str:
    return json.dumps(
        {
            "time": outcome.time.isoformat(),
            "origin": outcome.origin,
            "recorded": outcome.recorded,
            "replayed": outcome.replayed,
            "latency_ms": None if outcome.latency_ms is None else round(outcome.latency_ms, 1),
        }
    )


async def execute(args: Args) -> None:
    """Load the records, replay them and print the counts."""
    config = AlfredConfig.from_env()
    agent = build_agent(config, args.model)
    # Oldest first, so --limit keeps the oldest whatever order a file was written in.
    records = sorted(await _load(args, config), key=lambda record: record.time)
    if args.limit is not None:
        records = records[: args.limit]
    if not records:
        print("No records to replay.")
        return

    with contextlib.ExitStack() as stack:
        sink = stack.enter_context(_open_private(args.out)) if args.out is not None else None

        def keep(outcome: Outcome) -> None:
            if sink is not None:
                sink.write(_line(outcome) + "\n")
                sink.flush()

        outcomes = await replay(records, agent, on_outcome=keep)

    print(summarize(outcomes, total=len(records)))
    if len(outcomes) < len(records):
        _fail(f"stopped after {MAX_CONSECUTIVE_ERRORS} failed calls in a row; is the model up?", 1)


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(execute(parse_args(argv)))


if __name__ == "__main__":
    main()
