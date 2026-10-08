"""Checks over notifications: dispatched in the sample, or still held by do-not-disturb.

``Notification`` carries no channel: the delivery workers route by urgency
(``core/notifications/delivery.py``), so urgency is what a golden can ask about.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from pydantic import BaseModel, ConfigDict, model_validator

from evals.harness.checks.matching import describe
from evals.harness.checks.result import CheckResult, failed, passed
from evals.harness.evidence import Urgency  # noqa: TC001 — Pydantic resolves the params

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, NotificationRecord


class NotificationParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    urgency: Urgency | None = None
    source: str | None = None
    text: str | None = None  # in the title or the body, case-insensitive
    deferred: bool = False
    after_step: int | None = None

    @model_validator(mode="after")
    def _held_has_no_send_time(self) -> Self:
        if self.deferred and self.after_step is not None:
            raise ValueError("a deferred notification was never sent: after_step does not apply")
        return self


def _fits(n: NotificationRecord, p: NotificationParams) -> bool:
    return (
        (p.urgency is None or n.urgency == p.urgency)
        and (p.source is None or n.source == p.source)
        and (p.text is None or p.text.lower() in f"{n.title}\n{n.body}".lower())
    )


def _describe(n: NotificationRecord) -> str:
    return f"{n.urgency} {n.title!r} from {n.source}"


def notification(evidence: Evidence, p: NotificationParams) -> CheckResult:
    where = "deferred" if p.deferred else "sent"
    pool = list(evidence.deferred if p.deferred else evidence.notifications)
    if p.after_step is not None:
        start = evidence.step_start(p.after_step)
        pool = [n for n in pool if n.t is not None and n.t >= start]
    hits = [n for n in pool if _fits(n, p)]
    if hits:
        return passed("notification", f"{where}: {_describe(hits[0])}")
    want = describe(p.model_dump(exclude_none=True, exclude={"deferred"}))
    seen = "; ".join(_describe(n) for n in pool) or "none"
    return failed("notification", f"no {where} notification like {want}; {where}: {seen}")
