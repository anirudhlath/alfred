from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CheckResult(BaseModel):
    name: str
    status: Literal["pass", "fail", "error"]
    reason: str
    counted: bool = True


def passed(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="pass", reason=reason)


def failed(name: str, reason: str) -> CheckResult:
    return CheckResult(name=name, status="fail", reason=reason)
