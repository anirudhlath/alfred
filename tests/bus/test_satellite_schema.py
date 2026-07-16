"""Satellite channel schema — backward compatibility and new fields."""

import pytest
from pydantic import ValidationError

from bus.schemas.events import AlfredResponse, UserRequest


def test_user_request_accepts_satellite_channel() -> None:
    req = UserRequest(
        source="satellite",
        channel="satellite",
        session_id="sat-kitchen",
        identity_claim="sir",
        content_type="audio",
        content="turn off the lights",
        device_id="kitchen",
        area="Kitchen",
        identity_confidence=0.82,
    )
    assert req.device_id == "kitchen"
    assert req.area == "Kitchen"
    assert req.identity_confidence == 0.82


def test_user_request_new_fields_default_none() -> None:
    """Old-style payloads (web/iOS/signal) validate unchanged."""
    req = UserRequest(
        source="web-pwa",
        channel="web_pwa",
        session_id="s1",
        identity_claim="sir",
        content_type="text",
        content="hello",
    )
    assert req.device_id is None
    assert req.area is None
    assert req.identity_confidence is None


def test_user_request_roundtrip_without_new_fields() -> None:
    """JSON published by an older process (no new keys) still validates."""
    old_json = (
        '{"event_type": "user_request", "source": "web-pwa", "channel": "web_pwa",'
        ' "session_id": "s1", "identity_claim": "sir", "content_type": "text",'
        ' "content": "hi"}'
    )
    req = UserRequest.model_validate_json(old_json)
    assert req.identity_confidence is None


def test_alfred_response_accepts_satellite_channel() -> None:
    resp = AlfredResponse(
        source="conscious-engine", channel="satellite", session_id="sat-kitchen", text="Done."
    )
    assert resp.channel == "satellite"


def test_identity_confidence_bounds() -> None:
    with pytest.raises(ValidationError):
        UserRequest(
            source="satellite",
            channel="satellite",
            session_id="s",
            identity_claim="sir",
            content_type="audio",
            content="x",
            identity_confidence=1.5,
        )
