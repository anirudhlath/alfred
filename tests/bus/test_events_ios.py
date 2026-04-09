from bus.schemas.events import AlfredResponse, UserRequest


def test_user_request_accepts_ios_channel() -> None:
    req = UserRequest(
        source="ios-app",
        channel="ios",
        session_id="test-session",
        identity_claim="sir",
        content_type="text",
        content="hello",
    )
    assert req.channel == "ios"


def test_alfred_response_accepts_ios_channel() -> None:
    resp = AlfredResponse(
        source="conscious",
        channel="ios",
        session_id="test-session",
        text="hello",
    )
    assert resp.channel == "ios"
