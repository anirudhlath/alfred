"""Replay recorded Reflex decisions through a model, and count how they come out.

    python -m core.reflex.replay run --since 2026-10-26T23:35:00Z [--until ISO] [options]
    python -m core.reflex.replay run --records decisions.jsonl [options]
    python -m core.reflex.replay label (--since ISO [--until ISO] | --records FILE) --out FILE
    python -m core.reflex.replay score --labels FILE [--model MODEL] [--output SHAPE]

Each record's exact prompt goes to the model, one record at a time, through the same agent
and parser Reflex decides with (``core.reflex.agent``), with the tools the prompt showed
(``parse_tools``). Records come from the live decision log (``--since``, ``--until``) or from
a JSONL file with one ``DecisionRecord.model_dump_json()`` per line (``--records``). An event
recorded more than once (same ``event_id``) is replayed once, from its first record.

The replay only reads. It writes nothing to Redis, publishes nothing and starts no tracing, so
nothing it asks reaches an observation, a counter or a span. What it prints is counts only: the
duplicates it dropped, the recorded-by-replayed table, agreement, the invalid rate and model
latency p50 and p95. The prompts name the house and its people, so neither they nor the
reasons, entity ids or names in them are ever printed. ``--out`` writes one line per record (its
time and origin, the recorded and replayed decision, the latency), in a file only its owner can
read; keep it out of the repo.

``label`` marks each record ``ask`` or ``none`` by rules over the record alone (what it proposed,
whether it reverses a person, whether it repeats an earlier ask, quiet hours), so a candidate
model can be scored against the same labels each time. ``score`` decides every labelled prompt
again and counts how the candidate's asks agree with the labels. The rules and the counts are
``core.reflex.replay_labels``; this module reads, writes and prints. Both resolve targets
against the live state read once at the start, so the house's services must be registered. A
label file holds the owner's house: it is written readable only by its owner, and kept out of
the repo.

``--output`` (default ``REFLEX_OUTPUT``) picks how the model is asked to reply, ``text``, ``tool``,
``native`` or ``json`` (``core.reflex.agent``), so one recorded set can be decided in each shape and
the counts compared.

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

from pydantic import BaseModel

from bus.schemas.events import ReflexDecision
from core.reflex.agent import build_reflex_agent, decide
from core.reflex.context_reader import ContextReader
from core.reflex.decision_log import DecisionLog, DecisionRecord
from core.reflex.prompt import index_snapshot, parse_tools
from core.reflex.replay_labels import (
    Labelled,
    label_records,
    score_labels,
    share,
    summarize_labels,
)
from core.reflex.settings import load_settings
from shared.agents import resolve_model
from shared.config import REFLEX_OUTPUTS, AlfredConfig, ReflexOutput, apply_model_env
from shared.redis_streams import create_redis
from shared.usertime import get_user_timezone

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from bus.schemas.events import ReflexProposal
    from core.reflex.agent import ReflexAgent
    from core.reflex.prompt import LiveEntity
    from shared.types import AioRedis

# What a replayed call that raised (the server down, a timeout) is counted as.
ERROR: Final = "error"
# After this many failed calls in a row, stop rather than make a few hundred more, each of which
# may wait out its timeout.
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
    proposal: ReflexProposal | None = None  # what the model proposed; held for score, never printed


async def _decide_one(agent: ReflexAgent, record: DecisionRecord) -> Outcome:
    recorded = record.proposal.decision
    try:
        tools = parse_tools(record.prompt)
        started = time.perf_counter()  # after the parse, so the latency is the model call's
        proposal = await decide(agent, record.prompt, tools)
    except Exception as exc:  # a transport or provider failure: counted, not fatal
        return Outcome(record.time, record.origin, recorded, ERROR, None, type(exc).__name__)
    latency_ms = (time.perf_counter() - started) * 1000
    return Outcome(
        record.time, record.origin, recorded, proposal.decision, latency_ms, proposal=proposal
    )


async def replay(
    records: Sequence[DecisionRecord],
    agent: ReflexAgent,
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


def _percentile(sorted_values: Sequence[float], percent: int) -> float:
    """The nearest-rank percentile of an ascending, non-empty sequence."""
    rank = math.ceil(percent / 100 * len(sorted_values))
    return sorted_values[max(rank, 1) - 1]


def _timing_and_failures(answered: Sequence[Outcome], failed: Counter[str | None]) -> list[str]:
    """The model latency line, and the failed calls by exception class when there were any."""
    lines: list[str] = []
    latencies = sorted(o.latency_ms for o in answered if o.latency_ms is not None)
    if latencies:
        p50, p95 = _percentile(latencies, 50), _percentile(latencies, 95)
        lines.append(f"Model latency: p50 {p50:.0f} ms, p95 {p95:.0f} ms")
    else:
        lines.append("Model latency: no call was answered")
    if failed:
        lines.append("Failed calls: " + ", ".join(f"{name} x{n}" for name, n in failed.items()))
    return lines


def summarize(outcomes: Sequence[Outcome], *, total: int) -> str:
    """The counts: no prompt, entity, name or reason is printed, whatever the outcomes hold.

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
    lines.append(f"Agreement: {share(agreed, len(answered))}")
    replayed_invalid = sum(1 for o in answered if o.replayed == "invalid")
    recorded_invalid = sum(1 for o in answered if o.recorded == "invalid")
    lines.append(
        f"Invalid replies: {share(replayed_invalid, len(answered), 'replayed')};"
        f" {share(recorded_invalid, len(answered), 'recorded')}"
    )
    lines.extend(_timing_and_failures(answered, failed))
    return "\n".join(lines)


@dataclass(frozen=True)
class Args:
    """The ``run`` command's options, checked: exactly one of ``since`` and ``records``."""

    since: datetime | None
    until: datetime | None
    records: Path | None
    limit: int | None
    model: str | None
    output: ReflexOutput | None
    out: Path | None


@dataclass(frozen=True)
class LabelArgs:
    """The ``label`` command's options, checked: exactly one of ``since`` and ``records``."""

    since: datetime | None
    until: datetime | None
    records: Path | None
    out: Path


@dataclass(frozen=True)
class ScoreArgs:
    """The ``score`` command's options, checked: ``labels`` is a file."""

    labels: Path
    model: str | None
    output: ReflexOutput | None


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


def _add_source(command: argparse.ArgumentParser) -> None:
    """The options that say where the records come from: the live log or a file."""
    command.add_argument("--since", type=_moment, metavar="ISO", help="live log: from this time on")
    command.add_argument(
        "--until",
        type=_moment,
        metavar="ISO",
        help="live log, with --since: up to, not including, this time (default: now)",
    )
    command.add_argument(
        "--records", type=Path, metavar="FILE", help="JSONL: one DecisionRecord dump per line"
    )


def _add_model(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--model",
        metavar="MODEL",
        help="the model to decide with, e.g. vllm:<served name> (default: REFLEX_MODEL)",
    )


def _add_output(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--output",
        choices=REFLEX_OUTPUTS,
        metavar="SHAPE",
        help=(
            f"how the model's reply is asked for: {', '.join(REFLEX_OUTPUTS)} "
            "(default: REFLEX_OUTPUT)"
        ),
    )


def _parsers() -> tuple[argparse.ArgumentParser, dict[str, argparse.ArgumentParser]]:
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
    _add_source(run)
    run.add_argument(
        "--limit",
        type=_count,
        metavar="N",
        help="replay at most N records, the oldest first (default: all)",
    )
    _add_model(run)
    _add_output(run)
    run.add_argument(
        "--out",
        type=Path,
        metavar="FILE",
        help="write one JSONL line per record (private, mode 0600; keep it out of the repo)",
    )
    label = commands.add_parser(
        "label",
        help="label each recorded decision ask or none, into a private file",
        description=(
            "Label each recorded decision ask or none by rules over the record alone, and write"
            " one JSONL line per record to --out. Give exactly one of --since (the live decision"
            " log) and --records. Targets are resolved against the live state, read once. Prints"
            " counts only; nothing is written to Redis or published."
        ),
    )
    _add_source(label)
    label.add_argument(
        "--out",
        type=Path,
        required=True,
        metavar="FILE",
        help="the labels file (private, mode 0600; keep it out of the repo)",
    )
    score = commands.add_parser(
        "score",
        help="decide labelled prompts again and count wrong, missed and other asks",
        description=(
            "Decide the prompts of a label file again, one at a time, and print counts only:"
            " the asks the labels did not want, the asks they wanted and did not get, and the"
            " rest. Targets are resolved against the live state, read once. Nothing is written"
            " to Redis or published."
        ),
    )
    score.add_argument(
        "--labels", type=Path, required=True, metavar="FILE", help="a file `label` wrote"
    )
    _add_model(score)
    _add_output(score)
    return parser, {"run": run, "label": label, "score": score}


def _check_source(command: argparse.ArgumentParser, ns: argparse.Namespace) -> None:
    """A usage error unless exactly one source is given and its options go together."""
    if (ns.since is None) == (ns.records is None):
        command.error("give exactly one of --since (the live log) and --records (a JSONL file)")
    if ns.until is not None:
        if ns.since is None:
            command.error("--until goes with --since")
        if ns.until <= ns.since:
            command.error("--until must be after --since")
    if ns.records is not None and not ns.records.is_file():
        command.error(f"--records: {ns.records} is not a file")


def parse_args(argv: Sequence[str] | None = None) -> Args | LabelArgs | ScoreArgs:
    """The command line, or a usage error (exit status 2)."""
    parser, commands = _parsers()
    ns = parser.parse_args(argv)
    command = commands[ns.command]
    if ns.command == "score":
        if not ns.labels.is_file():
            command.error(f"--labels: {ns.labels} is not a file")
        return ScoreArgs(ns.labels, ns.model, ns.output)
    _check_source(command, ns)
    if ns.command == "label":
        return LabelArgs(ns.since, ns.until, ns.records, ns.out)
    return Args(ns.since, ns.until, ns.records, ns.limit, ns.model, ns.output, ns.out)


def _fail(message: str, code: int) -> NoReturn:
    print(f"{_PROG}: error: {message}", file=sys.stderr)
    raise SystemExit(code)


def build_agent(
    config: AlfredConfig, model: str | None, output: ReflexOutput | None
) -> ReflexAgent:
    """The Reflex agent over *model* and *output*, or over ``REFLEX_MODEL`` and ``REFLEX_OUTPUT``.

    Exits non-zero when no model resolves; ``resolve_model`` logs why. Tracing is not
    started, so the agent's spans go nowhere: a replayed prompt is never exported.
    """
    apply_model_env(config)
    setting = "REFLEX_MODEL" if model is None else "--model"
    resolved = resolve_model(setting, config.reflex_model if model is None else model)
    if resolved is None:
        _fail("no model: set REFLEX_MODEL, or pass --model (vllm:<served name>)", 1)
    return build_reflex_agent(resolved, config.reflex_output if output is None else output)


def _read_jsonl[M: BaseModel](path: Path, model: type[M], what: str) -> list[M]:
    """Every line of a JSONL file as a *model*; a bad line is a usage error naming its number."""
    lines: list[M] = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                lines.append(model.model_validate_json(line))
            except ValueError:  # the message would quote the line, prompt and all
                _fail(f"line {number} of {path} is not {what}", 2)
    return lines


def read_records_file(path: Path) -> list[DecisionRecord]:
    """Every record in a JSONL file; a bad line is a usage error naming its number only."""
    return _read_jsonl(path, DecisionRecord, "a decision record")


def read_labels_file(path: Path) -> list[Labelled]:
    """Every line of a label file; a bad line is a usage error naming its number only."""
    return _read_jsonl(path, Labelled, "a label")


async def _load(
    args: Args | LabelArgs, config: AlfredConfig, redis: AioRedis | None = None
) -> list[DecisionRecord]:
    """The records *args* names: its file, or the live log (through *redis*, else its own)."""
    if args.records is not None:
        return read_records_file(args.records)
    if args.since is None:
        raise ValueError("neither --records nor --since")
    if redis is None:
        own = create_redis(config.redis_url)
        try:
            return await _load(args, config, own)
        finally:
            await own.aclose()
    return await DecisionLog(redis).read(since=args.since, until=args.until)


def drop_duplicate_events(
    records: Sequence[DecisionRecord],
) -> tuple[list[DecisionRecord], int]:
    """*records* keeping each event's first record only, and how many records were dropped.

    A crash after a decision is recorded but before its event is marked decided records the
    event again on redelivery, and a log written before the marker existed holds every
    redelivery. Records are matched on ``event["event_id"]``; one without an id is kept.
    """
    seen: set[str] = set()
    kept: list[DecisionRecord] = []
    for record in records:
        event_id = record.event.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen:
                continue
            seen.add(event_id)
        kept.append(record)
    return kept, len(records) - len(kept)


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


def summarize_scores(
    lines: Sequence[Labelled], outcomes: Sequence[Outcome], house: Mapping[str, LiveEntity]
) -> str:
    """How a candidate's asks compare with the labels: counts only, nothing of the house.

    The counts are ``score_labels``'s; this adds the header and the model's timing. *outcomes*
    may be shorter than *lines*, a run that stopped early, and a call that raised is not scored.
    """
    answers = [
        (line, outcome.proposal)
        for line, outcome in zip(lines, outcomes, strict=False)
        if outcome.replayed != ERROR and outcome.proposal is not None
    ]
    answered = [o for o in outcomes if o.replayed != ERROR]
    failed = Counter(o.error for o in outcomes if o.replayed == ERROR)
    out = [
        f"Scored {len(outcomes)} of {len(lines)} records, {sum(failed.values())} failed.",
        "",
        *score_labels(answers, house).lines(),
    ]
    out.extend(_timing_and_failures(answered, failed))
    return "\n".join(out)


def _distinct(records: Sequence[DecisionRecord]) -> list[DecisionRecord]:
    """*records* oldest first, each event once; the count dropped is printed, never an id."""
    ordered = sorted(records, key=lambda record: record.time)
    kept, dropped = drop_duplicate_events(ordered)
    if dropped:
        print(f"Dropped {dropped} duplicate {'record' if dropped == 1 else 'records'}.")
    return kept


def _stop_on_errors(outcomes: Sequence[Outcome], total: int) -> None:
    """Exit non-zero, naming the exception classes, when the run stopped before the end."""
    if len(outcomes) < total:
        last = Counter(o.error for o in outcomes[-MAX_CONSECUTIVE_ERRORS:])
        names = ", ".join(f"{name} x{n}" for name, n in last.items())
        _fail(f"stopped after {MAX_CONSECUTIVE_ERRORS} consecutive errors: {names}", 1)


async def _house(redis: AioRedis) -> dict[str, LiveEntity]:
    """The live state by entity ID, read once; none at all is an exit with status 1."""
    snapshot = await ContextReader(redis).get_snapshot()
    house = {} if snapshot is None else index_snapshot(snapshot)
    if not house:
        _fail("no live state to resolve targets against; start the house's services first", 1)
    return house


async def _run(args: Args) -> None:
    """Load the records, replay them and print the counts."""
    config = AlfredConfig.from_env()
    agent = build_agent(config, args.model, args.output)
    # Oldest first, so --limit keeps the oldest whatever order a file was written in; before the
    # limit, so --limit N replays N events.
    records = _distinct(await _load(args, config))
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
    _stop_on_errors(outcomes, len(records))


async def _label(args: LabelArgs) -> None:
    """Label the records, write the label file and print the counts."""
    config = AlfredConfig.from_env()
    redis = create_redis(config.redis_url)
    try:
        loaded = await _load(args, config, redis)
        house = await _house(redis)
        settings = await load_settings(redis)
        tz_name = await get_user_timezone(redis)
    finally:
        await redis.aclose()
    labelled = label_records(_distinct(loaded), house, settings, tz_name)
    with _open_private(args.out) as sink:
        for line in labelled:
            sink.write(line.model_dump_json() + "\n")
    print(summarize_labels(labelled))


async def _score(args: ScoreArgs) -> None:
    """Decide every labelled prompt again and print how the asks compare with the labels."""
    config = AlfredConfig.from_env()
    lines = read_labels_file(args.labels)
    if not lines:
        print("No labelled records to score.")
        return
    redis = create_redis(config.redis_url)
    try:
        house = await _house(redis)
    finally:
        await redis.aclose()
    agent = build_agent(config, args.model, args.output)
    outcomes = await replay([line.record for line in lines], agent)
    print(summarize_scores(lines, outcomes, house))
    _stop_on_errors(outcomes, len(lines))


async def execute(args: Args | LabelArgs | ScoreArgs) -> None:
    """Run the command *args* is the options of."""
    if isinstance(args, LabelArgs):
        await _label(args)
    elif isinstance(args, ScoreArgs):
        await _score(args)
    else:
        await _run(args)


def main(argv: Sequence[str] | None = None) -> None:
    asyncio.run(execute(parse_args(argv)))


if __name__ == "__main__":
    main()
