"""IdentityGate — satellite channel resolution."""

from core.conscious.identity import IdentityGate


def _gate() -> IdentityGate:
    return IdentityGate(registered_phone="+15550001111")


def test_satellite_with_voiceprint_match_uses_voice_id() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="sir", authenticated=False, identity_confidence=0.85
    )
    assert result.identity == "sir"
    assert result.method == "voice_id"
    assert result.confidence == 0.85
    assert result.factors == ["voiceprint"]
    assert result.risk_clearance == "low"


def test_satellite_without_voiceprint_falls_back_to_local_claim() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="sir", authenticated=False, identity_confidence=None
    )
    assert result.identity == "sir"
    assert result.method == "local_claim"
    assert result.confidence == 0.7


def test_satellite_unknown_claim_is_guest() -> None:
    result = _gate().resolve(
        channel="satellite", identity_claim="guest", authenticated=False, identity_confidence=None
    )
    assert result.identity == "guest"


def test_existing_channels_unaffected() -> None:
    result = _gate().resolve(channel="web_pwa", identity_claim="sir", authenticated=False)
    assert result.method == "local_claim"
    assert result.confidence == 0.7
