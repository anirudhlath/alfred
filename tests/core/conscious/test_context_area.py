"""ContextAssembler — satellite area injection."""

from core.conscious.context_assembler import ContextAssembler
from core.identity.schemas import IdentityResult


def _sir() -> IdentityResult:
    return IdentityResult(
        identity="sir",
        confidence=0.9,
        method="voice_id",
        factors=["voiceprint"],
        risk_clearance="low",
    )


def test_area_injects_location_section() -> None:
    prompt = ContextAssembler().assemble(
        identity=_sir(),
        tools_section="",
        channel="satellite",
        content_type="audio",
        area="Kitchen",
    )
    assert "## Location" in prompt
    assert "Kitchen" in prompt


def test_no_area_no_location_section() -> None:
    prompt = ContextAssembler().assemble(
        identity=_sir(), tools_section="", channel="web_pwa", content_type="text"
    )
    assert "## Location" not in prompt


def test_satellite_is_a_voice_channel() -> None:
    """Voice-delivery prompt engages for satellite audio requests."""
    assembler = ContextAssembler()
    assert "satellite" in assembler._VOICE_CHANNELS
