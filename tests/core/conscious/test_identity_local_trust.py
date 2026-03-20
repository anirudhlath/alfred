"""Tests for local-device identity trust on web_pwa channel."""

from __future__ import annotations

from core.conscious.identity import IdentityGate


def test_web_pwa_claim_sir_unauthenticated_resolves_sir() -> None:
    """Web PWA claiming 'sir' without auth should resolve as sir (local trust)."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="sir", authenticated=False)
    assert result.identity == "sir"
    assert result.method == "local_claim"
    assert result.confidence < 0.9  # Lower confidence than authenticated


def test_web_pwa_claim_guest_resolves_guest() -> None:
    """Web PWA claiming 'guest' should resolve as guest."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="guest", authenticated=False)
    assert result.identity == "guest"


def test_web_pwa_authenticated_still_high_confidence() -> None:
    """Web PWA with authentication should still get high confidence."""
    gate = IdentityGate(registered_phone="+1234567890")
    result = gate.resolve(channel="web_pwa", identity_claim="sir", authenticated=True)
    assert result.identity == "sir"
    assert result.confidence >= 0.99
    assert result.method == "webauthn"
