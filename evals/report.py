"""Human-readable terminal output for eval runs and comparisons."""

from __future__ import annotations

from evals.compare import RunComparison, VerdictChange
from evals.models import EvalRun, Verdict

_VERDICT_SYMBOLS = {
    Verdict.PASS: "\u2713",  # check mark
    Verdict.PARTIAL: "~",
    Verdict.FAIL: "\u2717",  # x mark
}


def format_run(run: EvalRun) -> str:
    """Format an EvalRun as a human-readable report."""
    lines: list[str] = []
    lines.append(
        f"Eval Run: {run.timestamp.isoformat(timespec='seconds')}  "
        f"|  Model: {run.model}  |  {run.scenario_count} scenarios"
    )
    lines.append("")

    for result in run.results:
        sym = _VERDICT_SYMBOLS[result.verdict]
        name = result.scenario.name
        verdict_str = result.verdict.value.upper()
        latency = result.trace.latency_ms
        line = f"  {sym} {name} {'.' * max(1, 40 - len(name))} {verdict_str:>7}   ({latency:.0f}ms)"
        lines.append(line)
        if result.verdict != Verdict.PASS:
            lines.append(f"    -> {result.reason}")

    lines.append("")
    p = run.summary.get(Verdict.PASS, 0)
    pt = run.summary.get(Verdict.PARTIAL, 0)
    f = run.summary.get(Verdict.FAIL, 0)
    lines.append(f"Summary: {p} pass | {pt} partial | {f} fail")

    return "\n".join(lines)


def format_comparison(comp: RunComparison) -> str:
    """Format a RunComparison as a human-readable diff."""
    lines: list[str] = []
    lines.append(f"Comparing: {comp.old_run_id} -> {comp.new_run_id}")
    lines.append("")

    for c in comp.comparisons:
        old_v = c.old_verdict.value.upper()
        new_v = c.new_verdict.value.upper()
        name = c.name
        suffix = ""
        if c.change == VerdictChange.IMPROVED:
            suffix = "  improved"
        elif c.change == VerdictChange.REGRESSED:
            suffix = "  REGRESSED"
        line = (
            f"  {name} {'.' * max(1, 40 - len(name))} "
            f"{old_v} -> {new_v}   "
            f"({c.old_latency_ms:.0f}ms -> {c.new_latency_ms:.0f}ms)"
            f"{suffix}"
        )
        lines.append(line)

    if comp.added_scenarios:
        lines.append("")
        for name in comp.added_scenarios:
            lines.append(f"  + {name} (new scenario)")
    if comp.removed_scenarios:
        lines.append("")
        for name in comp.removed_scenarios:
            lines.append(f"  - {name} (removed scenario)")

    lines.append("")
    lines.append(
        f"Verdicts: +{comp.improved} improved | "
        f"{comp.regressed} regressed | "
        f"{comp.unchanged} unchanged"
    )

    # Avg latency
    if comp.comparisons:
        old_avg = sum(c.old_latency_ms for c in comp.comparisons) / len(comp.comparisons)
        new_avg = sum(c.new_latency_ms for c in comp.comparisons) / len(comp.comparisons)
        pct = ((new_avg - old_avg) / old_avg * 100) if old_avg > 0 else 0
        sign = "+" if pct > 0 else ""
        lines.append(f"Avg latency: {old_avg:.0f}ms -> {new_avg:.0f}ms ({sign}{pct:.0f}%)")

    return "\n".join(lines)
