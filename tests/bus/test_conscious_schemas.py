"""Tests for UserRequest and AlfredResponse schemas."""

from __future__ import annotations

from bus.schemas.events import AlfredResponse, UserRequest


def test_user_request_defaults() -> None:
    req = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="sess-1",
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )
    assert req.event_type == "user_request"
    assert req.audio_ref is None
    assert req.event_id  # auto-generated


def test_alfred_response_defaults() -> None:
    resp = AlfredResponse(
        source="conscious-engine",
        channel="web_pwa",
        session_id="sess-1",
        text="Good morning, sir.",
        actions_taken=["checked calendar"],
        mood="pleased",
    )
    assert resp.event_type == "alfred_response"
    assert resp.voice_audio_ref is None
