"""IdentityGate — resolves user identity before the Conscious Engine processes a request."""

from __future__ import annotations

import logging

from core.identity.schemas import IdentityResult

logger = logging.getLogger(__name__)

# Identity constants
IDENTITY_SIR = "sir"
IDENTITY_GUEST = "guest"


class IdentityGate:
    """Resolves identity from channel-specific claims.

    Phase 3 initial: Signal phone + WebAuthn session.
    Voice ID (SpeechBrain) added in Phase 3 Step 5.
    """

    def __init__(self, registered_phone: str) -> None:
        self._registered_phone = registered_phone

    def resolve_signal(self, sender_phone: str) -> IdentityResult:
        """Resolve identity from a Signal message sender."""
        if sender_phone == self._registered_phone:
            return IdentityResult(
                identity=IDENTITY_SIR,
                confidence=0.95,
                method="signal_phone",
                factors=["signal_phone"],
                risk_clearance="medium",
            )
        return IdentityResult(
            identity=IDENTITY_GUEST,
            confidence=1.0,
            method="signal_phone",
            factors=["signal_phone"],
            risk_clearance="low",
        )

    def resolve_session(self, authenticated: bool) -> IdentityResult:
        """Resolve identity from a web session (WebAuthn)."""
        if authenticated:
            return IdentityResult(
                identity=IDENTITY_SIR,
                confidence=0.99,
                method="webauthn",
                factors=["webauthn"],
                risk_clearance="high",
            )
        return IdentityResult(
            identity=IDENTITY_GUEST,
            confidence=1.0,
            method="unauthenticated",
            factors=[],
            risk_clearance="low",
        )

    def resolve(
        self,
        channel: str,
        identity_claim: str,
        authenticated: bool,
    ) -> IdentityResult:
        """Unified resolution from a UserRequest's fields."""
        if channel == "signal":
            return self.resolve_signal(sender_phone=identity_claim)
        if channel in ("web_pwa", "voice"):
            # Authenticated session (WebAuthn) takes priority
            if authenticated:
                return self.resolve_session(authenticated=True)
            # Trust identity claim on local channels (pre-WebAuthn)
            if identity_claim == IDENTITY_SIR:
                return IdentityResult(
                    identity=IDENTITY_SIR,
                    confidence=0.7,
                    method="local_claim",
                    factors=["identity_claim"],
                    risk_clearance="low",
                )
            return self.resolve_session(authenticated=False)
        logger.warning("Unknown channel '%s', defaulting to guest", channel)
        return IdentityResult(
            identity=IDENTITY_GUEST,
            confidence=1.0,
            method="unknown",
            factors=[],
            risk_clearance="low",
        )
