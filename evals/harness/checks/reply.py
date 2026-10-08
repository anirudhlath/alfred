"""Checks over Alfred's replies. ``step`` indexes evidence.replies; -1 is the last."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.harness.checks.result import CheckResult, failed, passed

if TYPE_CHECKING:
    from evals.harness.evidence import Evidence, Reply

# How much of each reply a failure reason quotes, and of all of them together.
_QUOTE_CHARS = 120
_SEEN_CHARS = 480


class Needles(BaseModel):
    """Exactly one of ``text``, ``any`` or ``regex``: what to look for in a text."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    text: str | None = None
    any_of: list[str] | None = Field(default=None, alias="any")
    regex: str | None = None

    @field_validator("regex")
    @classmethod
    def _regex_compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"regex {value!r} does not compile: {exc}") from exc
        return value

    @model_validator(mode="after")
    def _exactly_one_needle(self) -> Self:
        if sum(x is not None for x in (self.text, self.any_of, self.regex)) != 1:
            raise ValueError("give exactly one of text, any, regex")
        needles = [n for n in (self.text, self.regex, *(self.any_of or [])) if n is not None]
        if self.any_of == [] or any(not n.strip() for n in needles):
            raise ValueError(
                "needles must not be empty or blank: an empty one matches every text, "
                "and a blank one nearly every text"
            )
        return self


class ReplyTextParams(Needles):
    step: int | Literal["any"] = -1


def _replies(evidence: Evidence, step: int | Literal["any"]) -> list[Reply]:
    if step == "any":
        return list(evidence.replies)
    try:
        return [evidence.replies[step]]
    except IndexError:
        return []


def hit(p: Needles, text: str) -> str | None:
    """The needle found in *text* (case-insensitive), or None."""
    lowered = text.lower()
    if p.text is not None:
        return p.text if p.text.lower() in lowered else None
    if p.any_of is not None:
        return next((n for n in p.any_of if n.lower() in lowered), None)
    assert p.regex is not None
    m = re.search(p.regex, text, re.IGNORECASE)
    return m.group(0) if m else None


def needle_text(p: Needles) -> str:
    if p.text is not None:
        return p.text
    if p.any_of is not None:
        return " | ".join(p.any_of)
    return f"/{p.regex}/"


def _quote(text: str) -> str:
    return repr(text[:_QUOTE_CHARS])


def _seen(replies: list[Reply]) -> str:
    """The replies, quoted, as many as fit in ``_SEEN_CHARS``, then how many were left out."""
    shown: list[str] = []
    for r in replies:
        quote = _quote(r.text)
        if shown and len("; ".join([*shown, quote])) > _SEEN_CHARS:
            break
        shown.append(quote)
    rest = len(replies) - len(shown)
    return "; ".join(shown) + (f"; +{rest} more replies" if rest else "")


def reply_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    replies = _replies(evidence, p.step)
    if not replies:
        return failed("reply_contains", f"no reply at step {p.step}")
    for r in replies:
        if (found := hit(p, r.text)) is not None:
            return passed("reply_contains", f"found {found!r}")
    return failed("reply_contains", f"{needle_text(p)} not in {_seen(replies)}")


def reply_not_contains(evidence: Evidence, p: ReplyTextParams) -> CheckResult:
    replies = _replies(evidence, p.step)
    if not replies:
        return failed("reply_not_contains", f"no reply at step {p.step}")
    for r in replies:
        if (found := hit(p, r.text)) is not None:
            return failed("reply_not_contains", f"reply contains {found!r}: {_quote(r.text)}")
    return passed("reply_not_contains", f"{needle_text(p)} absent")
