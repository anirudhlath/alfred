"""The scorecard: per golden, per PRD row, and LLM usage by role."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, cast, get_args

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

from evals.harness.checks.judge_spec import JudgeCategory
from evals.harness.checks.result import CheckResult
from evals.harness.evidence import Evidence, Role

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from inspect_ai.log import EvalLog, EvalSample

SCORER_NAME = "scenario_scorer"
Value = Literal["C", "I", "N", "E"]
Status = Literal["shipped", "pending"]
CELL_CHARS = 200  # room for the sample id each failing entry starts with
UNKNOWN = "—"
_CHECKS = TypeAdapter(list[CheckResult])


class LlmUsage(BaseModel):
    role: Role
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class SampleRun(BaseModel):
    sample_id: str
    scenario_id: str
    variant: int
    epoch: int
    suite: str
    status: Status
    prd: list[str]
    value: Value
    checks: list[CheckResult] = Field(default_factory=list)
    error: str | None = None
    reply_ms: list[float] = Field(default_factory=list)
    llm: list[LlmUsage] = Field(default_factory=list)


class CheckTally(BaseModel):
    """One of a sample's checks over its runs: how often it passed, failed or errored."""

    index: int  # its place in the golden's ``expect`` list
    name: str
    counted: bool  # False when it never counts toward the verdict (an untrusted judge)
    passed: int = 0
    failed: int = 0
    errored: int = 0


class SampleSummary(BaseModel):
    """One sample, i.e. one ``(scenario_id, variant)``, over its epochs."""

    sample_id: str
    variant: int
    runs: int
    passes: int
    errors: int
    inconclusive: int
    pass_rate: float | None
    # Every run scored C. None (shown —) when a run errored or was inconclusive: an E is
    # the harness failing, never Alfred, so it cannot decide this either way.
    pass_k: bool | None
    flaky: bool  # 0 < pass rate < 1
    checks: list[CheckTally]


class GoldenSummary(BaseModel):
    scenario_id: str
    suite: str
    status: Status
    prd: list[str]
    variants: int
    variants_pass_k: int  # samples whose pass^k holds
    runs: int
    passes: int
    errors: int
    inconclusive: int
    pass_rate: float | None
    # False when any sample's pass^k fails, None when none fails but one is unknown.
    pass_k: bool | None
    flaky: bool  # one of its samples is
    checks_passed: int  # counted check results that passed, over every run
    checks_counted: int
    failing: list[str]  # each entry starts with the sample id it came from
    samples: list[SampleSummary]


class PrdRowSummary(BaseModel):
    prd_id: str
    goldens: list[str]
    pass_rate: float | None
    pass_k: int  # goldens whose pass^k holds
    pass_k_unknown: int  # goldens whose pass^k is unknown (an E or N run)
    flaky: int  # flaky goldens
    errors: int  # E runs


class RoleUsage(BaseModel):
    role: Role
    calls: int
    prompt_tokens: int
    completion_tokens: int
    p50_ms: float
    p95_ms: float


class RunMeta(BaseModel):
    run_dir: str
    model: str
    alfred_commit: str
    home_service_commit: str
    epochs: int
    calibration: dict[str, float]
    trusted: list[str]
    stacks: list[dict[str, Any]]
    # What went wrong with the run as a whole (see ``log_problems``), shown above the tables.
    problems: list[str] = Field(default_factory=list)
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Scorecard(BaseModel):
    meta: RunMeta
    goldens: list[GoldenSummary]
    prd_rows: list[PrdRowSummary]
    llm: list[RoleUsage]
    reply_p50_ms: float | None
    reply_p95_ms: float | None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank: the smallest value with at least ``q`` of the list at or below it."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _flat(text: str) -> str:
    return " ".join(text.split())


def log_problems(logs: Sequence[EvalLog], epochs: int) -> list[str]:
    """One line per log that did not succeed, and per sample epoch it never recorded."""
    problems: list[str] = []
    for log in logs:
        task = log.eval.task
        if log.status != "success":
            message = _flat(log.error.message) if log.error is not None else "no error recorded"
            problems.append(f"{task}: {log.status} — {message}")
        if log.samples is None:
            problems.append(f"{task}: samples not loaded — cannot check for missing runs")
            continue
        sample_ids = log.eval.dataset.sample_ids
        if sample_ids is None:
            continue
        recorded = {(str(s.id), s.epoch) for s in log.samples}
        problems += [
            f"{task}: sample {sid} epoch {epoch} is missing from the log"
            for sid in sample_ids
            for epoch in range(1, epochs + 1)
            if (str(sid), epoch) not in recorded
        ]
    return problems


def runs_from_logs(logs: Sequence[EvalLog]) -> list[SampleRun]:
    """One run per sample epoch. A sample whose data cannot be read becomes an E run."""
    runs: list[SampleRun] = []
    for log in logs:
        for sample in log.samples or []:
            try:
                runs.append(_sample_run(sample))
            except (TypeError, ValueError) as exc:  # ValueError covers ValidationError
                runs.append(_unreadable_run(sample, f"unreadable sample: {_describe(exc)}"))
    return runs


def _describe(exc: Exception, *where: str) -> str:
    """The problem in one line.

    A validation error names its first failing field (prefixed by ``where``, the path to
    the data that was validated) and counts the rest; anything else gives its first line.
    """
    if isinstance(exc, ValidationError) and (errors := exc.errors()):
        first = errors[0]
        loc = ".".join(str(part) for part in (*where, *first["loc"]))
        more = f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""
        return _flat(f"{loc}: {first['msg']}" if loc else first["msg"]) + more
    lines = str(exc).strip().splitlines()
    return _flat(lines[0]) if lines else type(exc).__name__


def _also(error: str | None, problem: str) -> str:
    return f"{error}; {problem}" if error else problem


def _sample_run(sample: EvalSample) -> SampleRun:
    md = sample.metadata or {}
    score = (sample.scores or {}).get(SCORER_NAME)
    value: Value = "E"
    error: str | None = None
    checks: list[CheckResult] = []
    if sample.error is not None or score is None:
        error = sample.error.message if sample.error is not None else "not scored"
    elif (score_value := str(score.value)) not in get_args(Value):
        error = f"unexpected score value {score_value}"
    else:
        value = cast("Value", score_value)
        try:
            checks = _CHECKS.validate_python((score.metadata or {}).get("checks", []))
        except (TypeError, ValueError) as exc:
            # The score itself stands; only its explanation is lost.
            error = f"unreadable checks: {_describe(exc, 'checks')}"
    status = md.get("status", "shipped")
    if status not in get_args(Status):
        # Counted as shipped so the run still shows up; the error says why it failed.
        value, error = "E", _also(error, f"unknown status {status}")
        status = "shipped"
    evidence: Evidence | None = None
    if raw := (sample.store or {}).get("evidence"):
        try:
            evidence = Evidence.model_validate(raw)
        except ValueError as exc:
            value, error = "E", _also(error, f"unreadable evidence: {_describe(exc, 'evidence')}")
    return SampleRun(
        sample_id=str(sample.id),
        scenario_id=str(md.get("scenario_id", sample.id)),
        variant=int(md.get("variant", 0)),
        epoch=sample.epoch,
        suite=str(md.get("suite", "")),
        status=status,
        prd=list(md.get("prd", [])),
        value=value,
        checks=checks,
        error=error,
        reply_ms=[r.latency_ms for r in evidence.replies] if evidence else [],
        llm=[
            LlmUsage(
                role=c.role,
                latency_ms=c.latency_ms,
                prompt_tokens=c.prompt_tokens,
                completion_tokens=c.completion_tokens,
            )
            for c in evidence.llm_calls
        ]
        if evidence
        else [],
    )


def _unreadable_run(sample: EvalSample, problem: str) -> SampleRun:
    """An E run built only from what cannot fail, so one bad sample never loses the report."""
    md = sample.metadata or {}
    variant, status, prd = md.get("variant"), md.get("status"), md.get("prd")
    return SampleRun(
        sample_id=str(sample.id),
        scenario_id=str(md.get("scenario_id", sample.id)),
        variant=variant if isinstance(variant, int) else 0,
        epoch=sample.epoch,
        suite=str(md.get("suite", "")),
        status=status if status in get_args(Status) else "shipped",
        prd=[p for p in prd if isinstance(p, str)] if isinstance(prd, list) else [],
        value="E",
        error=_also(sample.error.message if sample.error is not None else None, problem),
    )


def _counts(runs: list[SampleRun]) -> tuple[int, int, int, float | None]:
    """Passes, errors, inconclusive, and the pass rate ``C / (C + I)``."""
    passes = sum(r.value == "C" for r in runs)
    errors = sum(r.value == "E" for r in runs)
    inconclusive = sum(r.value == "N" for r in runs)
    scored = len(runs) - errors - inconclusive
    return passes, errors, inconclusive, passes / scored if scored else None


def _tallies(runs: list[SampleRun]) -> list[CheckTally]:
    tallies: dict[tuple[int, str], CheckTally] = {}
    for r in runs:
        for index, c in enumerate(r.checks):
            t = tallies.setdefault(
                (index, c.name), CheckTally(index=index, name=c.name, counted=True)
            )
            t.counted = t.counted and c.counted
            match c.status:
                case "pass":
                    t.passed += 1
                case "fail":
                    t.failed += 1
                case "error":
                    t.errored += 1
    return sorted(tallies.values(), key=lambda t: t.index)


def _sample(runs: list[SampleRun]) -> SampleSummary:
    passes, errors, inconclusive, pass_rate = _counts(runs)
    return SampleSummary(
        sample_id=runs[0].sample_id,
        variant=runs[0].variant,
        runs=len(runs),
        passes=passes,
        errors=errors,
        inconclusive=inconclusive,
        pass_rate=pass_rate,
        pass_k=None if errors or inconclusive else passes == len(runs),
        flaky=pass_rate is not None and 0 < pass_rate < 1,
        checks=_tallies(runs),
    )


def _golden(runs: list[SampleRun]) -> GoldenSummary:
    runs = sorted(runs, key=lambda r: (r.variant, r.epoch))
    first = runs[0]
    by_variant: dict[int, list[SampleRun]] = defaultdict(list)
    for r in runs:
        by_variant[r.variant].append(r)
    samples = [_sample(rs) for rs in by_variant.values()]
    passes, errors, inconclusive, pass_rate = _counts(runs)
    pass_ks = [s.pass_k for s in samples]
    counted = [c for r in runs for c in r.checks if c.counted]
    return GoldenSummary(
        scenario_id=first.scenario_id,
        suite=first.suite,
        status=first.status,
        prd=first.prd,
        variants=len(samples),
        variants_pass_k=sum(k is True for k in pass_ks),
        runs=len(runs),
        passes=passes,
        errors=errors,
        inconclusive=inconclusive,
        pass_rate=pass_rate,
        pass_k=False if False in pass_ks else None if None in pass_ks else True,
        flaky=any(s.flaky for s in samples),
        checks_passed=sum(c.status == "pass" for c in counted),
        checks_counted=len(counted),
        failing=_failing(runs),
        samples=samples,
    )


def _failing(runs: list[SampleRun], limit: int = 3) -> list[str]:
    """Why a golden fell short: check failures, harness errors, judge errors, then notes,
    each starting with the sample id it came from.

    A harness error always keeps a place: when failures fill every slot, the first error
    takes the last one.
    """

    def counted(value: Value, status: str) -> list[str]:
        return [
            f"{r.sample_id}: {c.name}: {c.reason}"
            for r in runs
            if r.value == value
            for c in r.checks
            if c.counted and c.status == status
        ]

    errors = [f"{r.sample_id}: error: {r.error}" for r in runs if r.value == "E" and r.error]
    # A run that kept its score can still carry a note (e.g. unreadable checks); last place.
    notes = [f"{r.sample_id}: error: {r.error}" for r in runs if r.value != "E" and r.error]
    failing = list(dict.fromkeys([*counted("I", "fail"), *errors, *counted("N", "error"), *notes]))
    failing = failing[:limit]
    if errors and not set(errors) & set(failing):
        failing[-1] = errors[0]
    return failing


def summarize(runs: Sequence[SampleRun], meta: RunMeta) -> Scorecard:
    by_golden: dict[str, list[SampleRun]] = defaultdict(list)
    for r in runs:
        by_golden[r.scenario_id].append(r)
    goldens = sorted(
        (_golden(rs) for rs in by_golden.values()), key=lambda g: (g.suite, g.scenario_id)
    )
    by_row: dict[str, list[GoldenSummary]] = defaultdict(list)
    for g in goldens:
        if g.status == "shipped":
            for prd_id in g.prd:
                by_row[prd_id].append(g)
    rows = []
    for prd_id, gs in sorted(by_row.items()):
        rates = [g.pass_rate for g in gs if g.pass_rate is not None]
        rows.append(
            PrdRowSummary(
                prd_id=prd_id,
                goldens=[g.scenario_id for g in gs],
                pass_rate=sum(rates) / len(rates) if rates else None,
                pass_k=sum(g.pass_k is True for g in gs),
                pass_k_unknown=sum(g.pass_k is None for g in gs),
                flaky=sum(g.flaky for g in gs),
                errors=sum(g.errors for g in gs),
            )
        )
    usage: dict[Role, list[LlmUsage]] = defaultdict(list)
    for r in runs:
        for u in r.llm:
            usage[u.role].append(u)
    roles = [
        RoleUsage(
            role=role,
            calls=len(us),
            prompt_tokens=sum(u.prompt_tokens or 0 for u in us),
            completion_tokens=sum(u.completion_tokens or 0 for u in us),
            p50_ms=percentile([u.latency_ms for u in us], 0.5) or 0.0,
            p95_ms=percentile([u.latency_ms for u in us], 0.95) or 0.0,
        )
        for role, us in sorted(usage.items())
    ]
    replies = [ms for r in runs for ms in r.reply_ms]
    return Scorecard(
        meta=meta,
        goldens=goldens,
        prd_rows=rows,
        llm=roles,
        reply_p50_ms=percentile(replies, 0.5),
        reply_p95_ms=percentile(replies, 0.95),
    )


def _pct(value: float | None) -> str:
    return UNKNOWN if value is None else f"{value:.0%}"


def _of(held: int, total: int, unknown: int) -> str:
    """``2/3``, or ``1/3 (1 —)`` when some of the rest are unknown rather than failing."""
    return f"{held}/{total}" + (f" ({unknown} {UNKNOWN})" if unknown else "")


def _cell(text: str) -> str:
    """Free text made safe for one table cell: one line, pipes escaped, bounded."""
    return _flat(text).replace("|", "\\|")[:CELL_CHARS]


def _short(commit: str) -> str:
    """The sha cut to 7, keeping what follows it (``+dirty``, `` (image not rebuilt)``)."""
    rest = commit.lstrip("0123456789abcdef")
    return commit[: len(commit) - len(rest)][:7] + rest


def _judge_trust(m: RunMeta) -> str:
    if not m.calibration:
        return f"no judge calibration for {m.model} — every judge check is untrusted"
    untrusted = sorted(set(m.calibration) - set(m.trusted))
    uncalibrated = sorted(c for c in get_args(JudgeCategory) if c not in m.calibration)
    return (
        f"judge trusted: {', '.join(m.trusted) or 'none'}"
        + (f" · untrusted: {', '.join(untrusted)}" if untrusted else "")
        + (f" · uncalibrated: {', '.join(uncalibrated)}" if uncalibrated else "")
    )


def render_markdown(card: Scorecard) -> str:
    m = card.meta
    lines = [
        f"# Alfred eval scorecard — {m.finished_at:%Y-%m-%d %H:%M} UTC",
        "",
        f"model `{m.model}` · Alfred `{_short(m.alfred_commit)}` · "
        f"home-service `{_short(m.home_service_commit)}` · {m.epochs} epochs · {_judge_trust(m)}",
        "",
    ]
    for s in m.stacks:
        lines.append(
            f"- stack `{s.get('suite')}`: boot {s.get('boot_seconds', 0) or 0:.0f} s, "
            f"first reply {(s.get('first_reply_ms') or 0) / 1000:.1f} s, "
            f"recoveries {s.get('recoveries', 0)}"
        )
    if m.problems:
        lines += ["", "## Run problems", ""] + [f"- {_flat(p)}" for p in m.problems]
    lines += [
        "",
        "## PRD rows",
        "",
        "| PRD row | goldens | pass rate | pass^k | flaky | errors |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {r.prd_id} | {len(r.goldens)} | {_pct(r.pass_rate)} | "
        f"{_of(r.pass_k, len(r.goldens), r.pass_k_unknown)} | {r.flaky} | {r.errors} |"
        for r in card.prd_rows
    ]
    shipped = [g for g in card.goldens if g.status == "shipped"]
    for suite in sorted({g.suite for g in shipped}):
        lines += [
            "",
            f"## {suite}",
            "",
            "| golden | variants | runs | pass rate | variants passing all k | flaky | errors "
            "| checks passed | first failing check |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for g in (g for g in shipped if g.suite == suite):
            unknown = sum(s.pass_k is None for s in g.samples)
            checks = f"{g.checks_passed}/{g.checks_counted}" if g.checks_counted else UNKNOWN
            first = _cell(g.failing[0]) if g.failing else ""
            lines.append(
                f"| {g.scenario_id} | {g.variants} | {g.runs} | {_pct(g.pass_rate)} | "
                f"{_of(g.variants_pass_k, g.variants, unknown)} | {'⚠' if g.flaky else ''} | "
                f"{g.errors} | {checks} | {first} |"
            )
    pending = [g for g in card.goldens if g.status == "pending"]
    if pending:
        lines += [
            "",
            "## Not yet working (pending)",
            "",
            "| golden | PRD rows | pass rate |",
            "|---|---|---|",
        ]
        lines += [
            f"| {g.scenario_id} | {', '.join(g.prd)} | {_pct(g.pass_rate)} |" for g in pending
        ]
    lines += [
        "",
        "## LLM usage",
        "",
        "| role | calls | prompt tok | completion tok | p50 ms | p95 ms |",
        "|---|---|---|---|---|---|",
    ]
    lines += [
        f"| {u.role} | {u.calls} | {u.prompt_tokens} | {u.completion_tokens} | "
        f"{u.p50_ms:.0f} | {u.p95_ms:.0f} |"
        for u in card.llm
    ]
    if card.reply_p50_ms is not None:
        lines += [
            "",
            f"Reply latency: p50 {card.reply_p50_ms / 1000:.1f} s · "
            f"p95 {(card.reply_p95_ms or 0) / 1000:.1f} s",
        ]
    return "\n".join(lines) + "\n"


def write_report(card: Scorecard, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path, json_path = out_dir / "report.md", out_dir / "report.json"
    md_path.write_text(render_markdown(card))
    json_path.write_text(card.model_dump_json(indent=2))
    return md_path, json_path
