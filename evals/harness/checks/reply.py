"""Checks over Alfred's replies. ``step`` indexes evidence.replies; -1 is the last."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, Reply


class ReplyTextParams(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = None
    any_of: list[str] | None = Field(default=None, alias="any")
    regex: str | None = None
    step: int | Literal["any"] = -1

    @model_validator(mode="after")
    def _exactly_one_needle(self) -> Self:
        if sum(x is not None for x in (self.text, self.any_of, self.regex)) != 1:
            raise ValueError("give exactly one of text, any, regex")
        return self


def _replies(evidence: Evidence, step: int | Literal["any"]) -> list[Reply]:
    if step == "any":
        return list(evidence.replies)
    try:
        return [evidence.replies[step]]
    except IndexError:
        return []


def _hit(p: ReplyTextParams, text: str) -> str | None:
    lowered = text.lower()
    if p.text is not None:
        return p.text if p.text.lower() in lowered else None
    if p.any_of is not None:
        return next((n for n in p.any_of if n.lower() in lowered), None)
    assert p.regex is not None
    m = re.search(p.regex, text, re.IGNORECASE)
    return m.group(0) if m else None


def _needle(p: ReplyTextParams) -> str:
    return p.text or (" | ".join(p.any_of) if p.any_of else f"/{p.regex}/")


def reply_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    replies = _replies(evidence, p.step)
    if not replies:
        return failed("reply_contains", f"no reply at step {p.step}")
    for r in replies:
        if (hit := _hit(p, r.text)) is not None:
            return passed("reply_contains", f"found {hit!r}")
    return failed("reply_contains", f"{_needle(p)} not in {replies[-1].text[:200]!r}")


def reply_not_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    for r in _replies(evidence, p.step):
        if (hit := _hit(p, r.text)) is not None:
            return failed("reply_not_contains", f"reply contains {hit!r}: {r.text[:200]!r}")
    return passed("reply_not_contains", f"{_needle(p)} absent")
