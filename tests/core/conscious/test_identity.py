"""Tests for IdentityGate."""

from __future__ import annotations

from core.conscious.identity import IdentityGate
from core.identity.schemas import IdentityResult


def test_signal_phone_match_is_sir() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve_signal(sender_phone="+15551234567")
    assert result.identity == "sir"
    assert result.method == "signal_phone"
    assert result.risk_clearance == "medium"


def test_signal_phone_mismatch_is_guest() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve_signal(sender_phone="+15559999999")
    assert result.identity == "guest"


def test_webauthn_session_is_sir() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve_session(authenticated=True)
    assert result.identity == "sir"
    assert result.method == "webauthn"
    assert result.risk_clearance == "high"


def test_unauthenticated_session_is_guest() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve_session(authenticated=False)
    assert result.identity == "guest"


def test_resolve_from_request_signal() -> None:
    gate = IdentityGate(registered_phone="+15551234567")
    result = gate.resolve(
        channel="signal",
        identity_claim="+15551234567",
        authenticated=False,
    )
    assert result.identity == "sir"


def test_resolve_from_request_web_authenticated() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve(
        channel="web_pwa",
        identity_claim="",
        authenticated=True,
    )
    assert result.identity == "sir"


def test_resolve_from_request_web_unauthenticated() -> None:
    gate = IdentityGate(registered_phone="")
    result = gate.resolve(
        channel="web_pwa",
        identity_claim="",
        authenticated=False,
    )
    assert result.identity == "guest"
