"""System 2's prompt — satellite area injection."""

from core.conscious.context_assembler import assemble
from core.conscious.system2_prompts import VOICE_CHANNELS, repo_system2_prompts


def test_area_injects_location_section() -> None:
    prompts = repo_system2_prompts(
        who="sir",
        channel="satellite",
        content_type="audio",
        area="Kitchen",
        integrations_available=False,
    )
    prompt = assemble(prompts, who="sir")
    assert "## Location" in prompt
    assert "Kitchen" in prompt


def test_no_area_no_location_section() -> None:
    prompts = repo_system2_prompts(
        who="sir",
        channel="web_pwa",
        content_type="text",
        area=None,
        integrations_available=False,
    )
    assert "## Location" not in assemble(prompts, who="sir")


def test_satellite_is_a_voice_channel() -> None:
    """Voice-delivery prompt engages for satellite audio requests."""
    assert "satellite" in VOICE_CHANNELS
