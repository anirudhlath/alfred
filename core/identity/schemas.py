"""Identity resolution schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class IdentityResult(BaseModel):
    """Result of identity resolution."""

    identity: Literal["sir", "guest"]
    confidence: float
    method: str  # "voice_id", "signal_phone", "webauthn", "device_proximity"
    factors: list[str]
    risk_clearance: Literal["low", "medium", "high", "critical"]
