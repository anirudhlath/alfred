"""Tests for WebAuthn auth routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import AuthenticatorTransport

from core.identity.auth_routes import (
    _DEVICE_NAME_MAX_LEN,
    _pairing_fails_key,
    create_auth_router,
)
from core.identity.credentials import CredentialStore
from shared.streams import (
    AUTH_SESSION_PREFIX,
    WEBAUTHN_CHALLENGE_PREFIX,
    WEBAUTHN_PAIRING_FAILS_PREFIX,
    WEBAUTHN_PAIRING_KEY,
)
from tests.helpers import aiter_values

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from httpx import Response


@pytest.fixture
async def store(tmp_path: object) -> CredentialStore:
    import pathlib

    db_path = pathlib.Path(str(tmp_path)) / "credentials.db"
    s = CredentialStore(db_path)
    await s.initialize()
    return s


@pytest.fixture
def redis_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.set = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.delete = AsyncMock()
    mock.hset = AsyncMock()
    mock.expire = AsyncMock()
    mock.hgetall = AsyncMock(return_value={})
    return mock


@pytest.fixture
def app(store: CredentialStore, redis_mock: AsyncMock) -> FastAPI:
    app = FastAPI()
    router = create_auth_router(store=store, redis=redis_mock)
    app.include_router(router)
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


_CREDENTIAL_ID = "AQID"
_LAPTOP_CREDENTIAL_ID = "laptop-cred"
_CHALLENGE = b"\x01\x02\x03\x04"

_LOGIN_BODY: dict[str, object] = {
    "_challenge_id": "c1",
    "id": _CREDENTIAL_ID,
    "rawId": _CREDENTIAL_ID,
    "type": "public-key",
    "response": {
        "clientDataJSON": "e30",
        "authenticatorData": "e30",
        "signature": "e30",
    },
}

_REGISTER_RESPONSE: dict[str, object] = {
    "clientDataJSON": "e30",
    "attestationObject": "e30",
}

_REGISTER_BODY: dict[str, object] = {
    "_challenge_id": "c1",
    "_device_name": "Phone",
    "id": _CREDENTIAL_ID,
    "rawId": _CREDENTIAL_ID,
    "type": "public-key",
    "response": _REGISTER_RESPONSE,
}


def _build_client(
    store: CredentialStore,
    redis_mock: AsyncMock,
    challenge: bytes,
    wrap: Callable[[FastAPI], Any] | None,
) -> TestClient:
    """App + client with the stored challenge stubbed the way production returns it.

    The Redis pool runs with ``decode_responses=False``, so a stored challenge always
    comes back as bytes — the decode regressions below depend on that.
    """
    redis_mock.get = AsyncMock(return_value=bytes_to_base64url(challenge).encode())
    app = FastAPI()
    app.include_router(create_auth_router(store=store, redis=redis_mock))
    return TestClient(wrap(app) if wrap is not None else app)


async def _register_test_passkey(store: CredentialStore) -> None:
    await store.save_credential(
        credential_id=_CREDENTIAL_ID,
        public_key=b"\x03",
        sign_count=0,
        device_name="Phone",
        transports=["internal"],
    )


async def _register_laptop_passkey(store: CredentialStore) -> None:
    """A second passkey, so removing one is not removing the last one."""
    await store.save_credential(
        credential_id=_LAPTOP_CREDENTIAL_ID,
        public_key=b"\x04",
        sign_count=0,
        device_name="Laptop",
        transports=["usb"],
    )


async def _login(
    store: CredentialStore,
    redis_mock: AsyncMock,
    *,
    wrap: Callable[[FastAPI], Any] | None = None,
    headers: dict[str, str] | None = None,
    challenge: bytes = _CHALLENGE,
    verify: Callable[..., object] | None = None,
) -> Response:
    """Register a passkey, then POST an assertion to ``login/complete``."""

    def _default_verify(**_kwargs: object) -> MagicMock:
        result = MagicMock()
        result.new_sign_count = 1
        return result

    await _register_test_passkey(store)
    client = _build_client(store, redis_mock, challenge, wrap)
    with patch(
        "core.identity.auth_routes.verify_authentication_response",
        side_effect=verify or _default_verify,
    ):
        return client.post("/api/auth/login/complete", headers=headers, json=_LOGIN_BODY)


async def _register(
    store: CredentialStore,
    redis_mock: AsyncMock,
    *,
    challenge: bytes = _CHALLENGE,
    verify: Callable[..., object] | None = None,
) -> Response:
    """POST an attestation to ``register/complete`` on a freshly built app."""

    def _default_verify(**_kwargs: object) -> MagicMock:
        result = MagicMock()
        result.credential_id = b"\x01\x02"
        result.credential_public_key = b"\x03"
        result.sign_count = 0
        return result

    client = _build_client(store, redis_mock, challenge, None)
    with patch(
        "core.identity.auth_routes.verify_registration_response",
        side_effect=verify or _default_verify,
    ):
        return client.post("/api/auth/register/complete", json=_REGISTER_BODY)


def _behind_proxy(app: FastAPI) -> Any:
    """Wrap the app as uvicorn does once ``FORWARDED_ALLOW_IPS`` trusts the peer."""
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

    # uvicorn types its middleware against its own strict ASGIApplication
    # protocol, which starlette's looser scope signature never satisfies.
    return ProxyHeadersMiddleware(app, trusted_hosts="testclient")  # type: ignore[arg-type]


class _NoPeerApp:
    """ASGI shim that blanks the peer address, as an odd proxy or a unix socket does."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self._app({**scope, "client": None}, receive, send)


def _passkey_login(
    client: TestClient,
    redis_mock: AsyncMock,
    *,
    body_extra: dict[str, object] | None = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """Drive /login/complete with the WebAuthn signature check patched out."""
    redis_mock.get = AsyncMock(return_value=bytes_to_base64url(_CHALLENGE).encode())
    verification = MagicMock()
    verification.new_sign_count = 1
    body: dict[str, object] = {**_LOGIN_BODY, **(body_extra or {})}
    with patch(
        "core.identity.auth_routes.verify_authentication_response", return_value=verification
    ):
        return client.post("/api/auth/login/complete", json=body, headers=headers)


def _scanned_keys(sessions: dict[str, dict[bytes, bytes]]) -> list[bytes | str]:
    """Keys as SCAN hands them over: bytes on the production pool
    (``decode_responses=False``), with the first left as str so both branches of
    ``decode_stream_value`` are exercised."""
    return [key if index == 0 else key.encode() for index, key in enumerate(sessions)]


def _install_sessions(
    redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
) -> dict[str, dict[bytes, bytes]]:
    """Serve ``sessions`` from the redis mock the way the production pool does.

    Both reads are wired per call, so a test can add a session and have the scan
    see it — insertion order is the order SCAN yields.
    """
    redis_mock.hgetall = AsyncMock(side_effect=lambda key: sessions.get(key, {}))
    redis_mock.scan_iter = MagicMock(
        side_effect=lambda match="*", count=100: aiter_values(_scanned_keys(sessions))
    )
    redis_mock.ttl = AsyncMock(return_value=1200)
    redis_mock.delete = AsyncMock(return_value=1)
    return sessions


def _registration_options_patched() -> tuple[Any, Any]:
    """Patch the WebAuthn option builders so register/begin runs without a real RP."""
    mock_options = MagicMock()
    mock_options.challenge = b"\x01\x02\x03"
    return (
        patch("core.identity.auth_routes.generate_registration_options", return_value=mock_options),
        patch("core.identity.auth_routes.options_to_json", return_value='{"ok": true}'),
    )


_PAIRING_CODE = "123456"
_PAIRING_CODE_BYTES = _PAIRING_CODE.encode()  # how the production pool hands it back
_PAIRING_TTL_SECONDS = 300


# Two devices off the LAN, and the proxy in front of them (RFC 5737 documentation nets).
_DEVICE_A = "203.0.113.5"
_DEVICE_B = "198.51.100.9"
# The same two, over IPv6 (RFC 3849 documentation prefix). ``_V6_A``/``_V6_A_SIBLING``
# share a /64 the way two devices on one residential line do; ``_V6_OTHER_64`` does not.
_V6_A = "2001:db8::1"
_V6_A_SIBLING = "2001:db8::2"
_V6_OTHER_64 = "2001:db8:0:1::1"


def _serve_pairing_code(
    redis_mock: AsyncMock,
    stored: bytes | str = _PAIRING_CODE_BYTES,
    *,
    challenge: bytes | None = None,
) -> str:
    """Serve ``stored`` as the active pairing code and ``challenge`` for every other
    key, the way the production pool does — plus a real per-address guess counter
    behind INCR, so a test can spend a budget by making the requests rather than by
    stubbing a return value. The counter reads back as bytes, as the production pool
    (``decode_responses=False``) hands it over. Returns the code a client would send.
    """
    fails: dict[str, int] = {}

    async def _get(key: str) -> bytes | str | None:
        if key == WEBAUTHN_PAIRING_KEY:
            return stored
        if key.startswith(WEBAUTHN_PAIRING_FAILS_PREFIX):
            return str(fails[key]).encode() if key in fails else None
        return challenge

    async def _incr(key: str) -> int:
        fails[key] = fails.get(key, 0) + 1
        return fails[key]

    redis_mock.get = AsyncMock(side_effect=_get)
    redis_mock.incr = AsyncMock(side_effect=_incr)
    return _PAIRING_CODE


def _guess(client: TestClient, code: str) -> Response:
    """One ``register/begin`` attempt carrying ``code`` as the pairing header."""
    gen, to_json = _registration_options_patched()
    with gen, to_json:
        return client.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": code},
        )


def _spend_guesses(client: TestClient, n: int) -> None:
    """Burn ``n`` wrong guesses from ``client``'s address, asserting each is refused."""
    for _ in range(n):
        assert _guess(client, "000000").status_code == 403


def _register_complete_verification() -> MagicMock:
    """A verified attestation for ``_REGISTER_BODY``'s credential."""
    verification = MagicMock()
    verification.credential_id = b"\x01\x02\x03"
    verification.credential_public_key = b"\x03"
    verification.sign_count = 0
    return verification


def _session_record(credential_id: str, channel: str, created_at: str) -> dict[bytes, bytes]:
    """A session hash as the production pool returns it (decode_responses=False)."""
    return {
        b"authenticated": b"1",
        b"credential_id": credential_id.encode(),
        b"created_at": created_at.encode(),
        b"ip": b"203.0.113.7",
        b"user_agent": b"AlfredPWA/1.0",
        b"channel": channel.encode(),
    }


class TestAuthStatus:
    def test_no_credentials_registered(self, client: TestClient) -> None:
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] is False
        assert data["authenticated"] is False

    @pytest.mark.asyncio
    async def test_credential_registered_not_authenticated(
        self, store: CredentialStore, client: TestClient
    ) -> None:
        await store.save_credential(
            credential_id="dGVzdC1jcmVk",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal"],
        )
        resp = client.get("/api/auth/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["registered"] is True
        assert data["authenticated"] is False


async def _reject_network(request: Request) -> None:
    """Network gate that always rejects as untrusted."""
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")


async def _allow_network(request: Request) -> None:
    """Network gate that always passes."""


def _client_with_gate(
    store: CredentialStore,
    redis_mock: AsyncMock,
    gate: Callable[[Request], Awaitable[None]],
    *,
    peer: str | None = None,
) -> TestClient:
    """A client whose registration routes fall back to ``gate`` off the LAN.

    ``peer`` sets the socket address the app sees (``request.client.host``), which is
    what the per-address pairing budget is keyed on; the default is TestClient's own
    ``"testclient"``.
    """
    app = FastAPI()
    app.include_router(create_auth_router(store=store, redis=redis_mock, trusted_network_dep=gate))
    return TestClient(app) if peer is None else TestClient(app, client=(peer, 50000))


class TestRegistrationBegin:
    @pytest.mark.asyncio
    async def test_returns_options(self, client: TestClient, store: CredentialStore) -> None:
        """The options the route builds are the contract — the RP the passkey binds to,
        and the exclude list that stops a device registering a second passkey. Asserting
        only that the stub was called proves nothing about any of it."""
        await _register_test_passkey(store)
        gen, to_json = _registration_options_patched()

        with gen as mock_gen, to_json:
            resp = client.post("/api/auth/register/begin", json={"device_name": "MacBook Pro"})

        assert resp.status_code == 200
        kwargs = mock_gen.call_args.kwargs
        # The RP id is the request Host, which is what a passkey is bound to for life.
        assert kwargs["rp_id"] == "testserver"
        assert kwargs["rp_name"] == "Alfred"
        assert kwargs["user_name"] == "sir"
        assert kwargs["user_display_name"] == "Sir"
        # The existing passkey is excluded, as an enum member and not the stored string
        # — `options_to_json` calls `.value` on each transport.
        [descriptor] = kwargs["exclude_credentials"]
        assert descriptor.id == base64url_to_bytes(_CREDENTIAL_ID)
        assert descriptor.transports == [AuthenticatorTransport.INTERNAL]

        body = resp.json()
        assert body["ok"] is True  # from the patched options_to_json
        # The two fields the route adds itself, which the client sends back on complete.
        assert body["_device_name"] == "MacBook Pro"
        assert uuid.UUID(body["_challenge_id"])

    def test_rejects_untrusted_network(self, store: CredentialStore, redis_mock: AsyncMock) -> None:
        untrusted = _client_with_gate(store, redis_mock, _reject_network)

        resp = untrusted.post("/api/auth/register/begin", json={"device_name": "Test"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Access restricted to trusted networks"


class TestRegistrationComplete:
    def test_rejects_untrusted_network(self, store: CredentialStore, redis_mock: AsyncMock) -> None:
        untrusted = _client_with_gate(store, redis_mock, _reject_network)

        resp = untrusted.post("/api/auth/register/complete", json={"credential": "{}"})

        assert resp.status_code == 403


class TestRegistrationCompleteBodyValidation:
    """``register/complete`` reads a raw JSON body, and both the device name and the
    transports are *stored*. Anything the credential row cannot hold is refused before
    the ceremony — a 400 after ``verify_registration_response`` would leave a passkey
    saved on a request that failed."""

    @pytest.fixture
    def complete(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> Callable[[dict[str, object]], Response]:
        """POST ``register/complete`` with the signature check patched out."""
        redis_mock.get = AsyncMock(return_value=b"AQID")

        def _post(body_extra: dict[str, object]) -> Response:
            with patch(
                "core.identity.auth_routes.verify_registration_response",
                return_value=_register_complete_verification(),
            ):
                return client.post(
                    "/api/auth/register/complete", json={**_REGISTER_BODY, **body_extra}
                )

        return _post

    @pytest.mark.asyncio
    async def test_a_device_name_at_the_cap_is_accepted_and_stored(
        self, complete: Callable[[dict[str, object]], Response], store: CredentialStore
    ) -> None:
        """100 is the accept edge — the same ceiling ``register/begin`` enforces."""
        name = "n" * _DEVICE_NAME_MAX_LEN

        resp = complete({"_device_name": name})

        assert resp.status_code == 200
        cred = await store.get_credential("AQID")
        assert cred is not None
        assert cred.device_name == name

    def test_a_device_name_one_over_the_cap_is_refused(
        self, complete: Callable[[dict[str, object]], Response]
    ) -> None:
        resp = complete({"_device_name": "n" * (_DEVICE_NAME_MAX_LEN + 1)})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid device name"

    @pytest.mark.parametrize("name", [7, None, ["Phone"], {"name": "Phone"}, "", "   "])
    def test_a_device_name_that_is_not_a_usable_string_is_refused(
        self, complete: Callable[[dict[str, object]], Response], name: object
    ) -> None:
        """A non-string used to 500 *after* the passkey was saved: the row went to
        SQLite and the request failed anyway."""
        resp = complete({"_device_name": name})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid device name"

    @pytest.mark.asyncio
    async def test_an_absent_device_name_still_falls_back_to_the_default(
        self, client: TestClient, redis_mock: AsyncMock, store: CredentialStore
    ) -> None:
        redis_mock.get = AsyncMock(return_value=b"AQID")
        body = {k: v for k, v in _REGISTER_BODY.items() if k != "_device_name"}

        with patch(
            "core.identity.auth_routes.verify_registration_response",
            return_value=_register_complete_verification(),
        ):
            resp = client.post("/api/auth/register/complete", json=body)

        assert resp.status_code == 200
        cred = await store.get_credential("AQID")
        assert cred is not None
        assert cred.device_name == "Unknown Device"

    @pytest.mark.asyncio
    async def test_a_list_of_transport_strings_is_accepted(
        self, complete: Callable[[dict[str, object]], Response], store: CredentialStore
    ) -> None:
        resp = complete({"response": {**_REGISTER_RESPONSE, "transports": ["usb", "nfc"]}})

        assert resp.status_code == 200
        cred = await store.get_credential("AQID")
        assert cred is not None
        assert cred.transports == ["usb", "nfc"]

    @pytest.mark.parametrize("transports", [7, "usb", {"0": "usb"}, ["usb", 7], [None]])
    def test_transports_that_are_not_a_list_of_strings_are_refused(
        self, complete: Callable[[dict[str, object]], Response], transports: object
    ) -> None:
        """Stored, so the damage outlives the request: ``_to_transports`` TypeErrors on
        a non-string, and ``login/begin`` builds its allow-list from *every* stored
        credential — one bad row locks everyone out."""
        resp = complete({"response": {**_REGISTER_RESPONSE, "transports": transports}})

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid transports"

    def test_a_refused_body_never_reaches_the_ceremony(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        """The 400 lands before the challenge is consumed and before the signature is
        checked, so the client can fix the body and retry the same ceremony."""
        redis_mock.get = AsyncMock(return_value=b"AQID")

        with patch("core.identity.auth_routes.verify_registration_response") as verify:
            resp = client.post(
                "/api/auth/register/complete", json={**_REGISTER_BODY, "_device_name": 7}
            )

        assert resp.status_code == 400
        verify.assert_not_called()
        redis_mock.delete.assert_not_awaited()


class TestLoginBegin:
    @pytest.mark.asyncio
    async def test_returns_options_with_credentials(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await store.save_credential(
            credential_id="dGVzdC1jcmVk",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal"],
        )
        with (
            patch("core.identity.auth_routes.generate_authentication_options") as mock_gen,
            patch("core.identity.auth_routes.options_to_json") as mock_json,
        ):
            mock_options = MagicMock()
            mock_options.challenge = b"\x04\x05\x06"
            mock_gen.return_value = mock_options
            mock_json.return_value = '{"test": "auth_options"}'

            resp = client.post("/api/auth/login/begin")
            assert resp.status_code == 200
            data = resp.json()
            assert data["test"] == "auth_options"

    def test_returns_404_no_credentials(self, client: TestClient) -> None:
        resp = client.post("/api/auth/login/begin")
        assert resp.status_code == 404


class TestLogout:
    def test_clears_session_and_cookie(self, client: TestClient, redis_mock: AsyncMock) -> None:
        redis_mock.hgetall.return_value = {
            b"authenticated": b"1",
            b"credential_id": b"test",
            b"created_at": b"2026-04-16T00:00:00",
        }
        client.cookies.set("alfred_auth", "session-123")
        resp = client.post("/api/auth/logout")
        assert resp.status_code == 200
        redis_mock.delete.assert_called_once_with(f"{AUTH_SESSION_PREFIX}session-123")


class TestRegisterCompleteBytesDecode:
    """Regression: redis.get returns bytes when decode_responses=False (production pool).

    The bug: base64url_to_bytes(b'abc...') would f-string the REPR of the bytes
    object ("b'abc...'"), decoding to garbage and causing 401 on every passkey
    registration attempt.  The fix decodes bytes → str before passing to the helper.
    """

    @pytest.mark.asyncio
    async def test_register_complete_bytes_challenge_not_corrupted(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """verify_registration_response receives the correct expected_challenge bytes
        when redis returns the stored challenge as bytes (decode_responses=False)."""
        raw_challenge = b"\xde\xad\xbe\xef\xca\xfe"
        captured_kwargs: dict[str, object] = {}

        def _capture_verify(**kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.credential_id = b"\x01\x02"
            result.credential_public_key = b"\x03"
            result.sign_count = 0
            return result

        await _register(store, redis_mock, challenge=raw_challenge, verify=_capture_verify)

        # The fix must have fired: challenge was decoded to str before base64url_to_bytes
        assert "expected_challenge" in captured_kwargs, (
            "verify_registration_response was not called — challenge decode failed"
        )
        got = captured_kwargs["expected_challenge"]
        assert got == raw_challenge, f"expected_challenge corrupted by bytes repr: {got!r}"


class TestLoginCompleteBytesDecode:
    """Regression: same decode_responses=False bytes bug in login_complete."""

    @pytest.mark.asyncio
    async def test_login_complete_bytes_challenge_not_corrupted(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """verify_authentication_response receives the correct expected_challenge bytes
        when redis returns the stored challenge as bytes (decode_responses=False)."""
        raw_challenge = b"\xfe\xed\xfa\xce\xba\xbe"
        captured_kwargs: dict[str, object] = {}

        def _capture_verify(**kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.new_sign_count = 1
            return result

        await _login(store, redis_mock, challenge=raw_challenge, verify=_capture_verify)

        assert "expected_challenge" in captured_kwargs, (
            "verify_authentication_response was not called — challenge decode failed"
        )
        got = captured_kwargs["expected_challenge"]
        assert got == raw_challenge, f"expected_challenge corrupted by bytes repr: {got!r}"


class TestTransportEnumConversion:
    """Regression: stored transport strings must be coerced to AuthenticatorTransport enums.

    py_webauthn calls .value on each transport in options_to_json; passing plain str
    causes AttributeError: 'str' object has no attribute 'value'.  Both the
    register/begin excludeCredentials list and the login/begin allowCredentials list
    share the same bug — both paths are covered here.
    """

    @pytest.mark.asyncio
    async def test_login_begin_known_transports_round_trip(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """login/begin with transports ['internal', 'hybrid'] → 200, no AttributeError,
        and allowCredentials[0].transports carries both enum members."""
        await store.save_credential(
            credential_id="dGVzdC1jcmVk",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal", "hybrid"],
        )

        captured_kwargs: dict[str, object] = {}

        def _capture_gen(**kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.challenge = b"\x07\x08\x09"
            return result

        with (
            patch(
                "core.identity.auth_routes.generate_authentication_options",
                side_effect=_capture_gen,
            ),
            patch("core.identity.auth_routes.options_to_json", return_value='{"ok": true}'),
        ):
            from fastapi import FastAPI

            _app = FastAPI()
            from core.identity.auth_routes import create_auth_router

            _app.include_router(create_auth_router(store=store, redis=redis_mock))
            from fastapi.testclient import TestClient

            _client = TestClient(_app)
            response = _client.post("/api/auth/login/begin")

        assert response.status_code == 200
        assert "allow_credentials" in captured_kwargs
        descriptors = captured_kwargs["allow_credentials"]
        assert isinstance(descriptors, list) and len(descriptors) == 1
        from webauthn.helpers.structs import AuthenticatorTransport

        assert descriptors[0].transports == [
            AuthenticatorTransport.INTERNAL,
            AuthenticatorTransport.HYBRID,
        ]

    @pytest.mark.asyncio
    async def test_login_begin_unknown_transport_dropped(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """login/begin with transports ['internal', 'bogus'] → 200, 'bogus' silently dropped."""
        await store.save_credential(
            credential_id="dGVzdC1jcmVk",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal", "bogus"],
        )

        captured_kwargs: dict[str, object] = {}

        def _capture_gen(**kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.challenge = b"\x0a\x0b\x0c"
            return result

        with (
            patch(
                "core.identity.auth_routes.generate_authentication_options",
                side_effect=_capture_gen,
            ),
            patch("core.identity.auth_routes.options_to_json", return_value='{"ok": true}'),
        ):
            from fastapi import FastAPI

            _app = FastAPI()
            from core.identity.auth_routes import create_auth_router

            _app.include_router(create_auth_router(store=store, redis=redis_mock))
            from fastapi.testclient import TestClient

            _client = TestClient(_app)
            response = _client.post("/api/auth/login/begin")

        assert response.status_code == 200
        assert "allow_credentials" in captured_kwargs
        descriptors = captured_kwargs["allow_credentials"]
        assert isinstance(descriptors, list) and len(descriptors) == 1
        from webauthn.helpers.structs import AuthenticatorTransport

        # Only "internal" survives; "bogus" is dropped
        assert descriptors[0].transports == [AuthenticatorTransport.INTERNAL]

    @pytest.mark.asyncio
    async def test_register_begin_exclude_credentials_with_transports(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """register/begin with one existing credential → 200 and excludeCredentials present.

        This is the path that raises AttributeError once any credential exists because
        the stored transport strings were passed raw to PublicKeyCredentialDescriptor.
        """
        await store.save_credential(
            credential_id="dGVzdC1jcmVk",
            public_key=b"\x01",
            sign_count=0,
            device_name="Test",
            transports=["internal"],
        )

        captured_kwargs: dict[str, object] = {}

        def _capture_gen(**kwargs: object) -> MagicMock:
            captured_kwargs.update(kwargs)
            result = MagicMock()
            result.challenge = b"\x0d\x0e\x0f"
            return result

        with (
            patch(
                "core.identity.auth_routes.generate_registration_options",
                side_effect=_capture_gen,
            ),
            patch("core.identity.auth_routes.options_to_json", return_value='{"ok": true}'),
        ):
            from fastapi import FastAPI

            _app = FastAPI()
            from core.identity.auth_routes import create_auth_router

            _app.include_router(
                create_auth_router(
                    store=store,
                    redis=redis_mock,
                    trusted_network_dep=_allow_network,
                )
            )
            from fastapi.testclient import TestClient

            _client = TestClient(_app)
            response = _client.post(
                "/api/auth/register/begin",
                json={"device_name": "New Device"},
            )

        assert response.status_code == 200
        assert "exclude_credentials" in captured_kwargs
        exclude = captured_kwargs["exclude_credentials"]
        assert isinstance(exclude, list) and len(exclude) == 1
        from webauthn.helpers.structs import AuthenticatorTransport

        assert exclude[0].transports == [AuthenticatorTransport.INTERNAL]


class TestSessionLifetime:
    """Sessions last 8 hours (spec §3.2) and the cookie is Secure whenever the
    request arrived over HTTPS — including via a trusted reverse proxy."""

    @pytest.mark.asyncio
    async def test_session_and_cookie_last_eight_hours(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        resp = await _login(store, redis_mock)

        assert resp.status_code == 200
        _key, ttl = redis_mock.expire.call_args[0]
        assert ttl == 28800
        cookie = resp.headers["set-cookie"]
        assert "Max-Age=28800" in cookie
        assert "Secure" not in cookie  # plain http in this test

    @pytest.mark.asyncio
    async def test_registration_session_and_cookie_last_eight_hours(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """register/complete signs the new device in, so it gets the same short session."""
        resp = await _register(store, redis_mock)

        assert resp.status_code == 200
        _key, ttl = redis_mock.expire.call_args[0]
        assert ttl == 28800
        assert "Max-Age=28800" in resp.headers["set-cookie"]

    @pytest.mark.asyncio
    async def test_cookie_is_secure_behind_https_proxy(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        resp = await _login(
            store,
            redis_mock,
            wrap=_behind_proxy,
            headers={"X-Forwarded-Proto": "https"},
        )

        assert resp.status_code == 200
        assert "Secure" in resp.headers["set-cookie"]


class TestSessionMetadata:
    """Every session records where it came from (spec §10: sessions sheet)."""

    @pytest.mark.asyncio
    async def test_login_records_ip_user_agent_and_channel(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await _register_test_passkey(store)

        resp = _passkey_login(
            client,
            redis_mock,
            body_extra={"_channel": "pwa"},
            headers={"user-agent": "AlfredPWA/1.0"},
        )

        assert resp.status_code == 200
        key = redis_mock.hset.call_args[0][0]
        mapping = redis_mock.hset.call_args.kwargs["mapping"]
        assert key.startswith(AUTH_SESSION_PREFIX)
        assert mapping["authenticated"] == "1"
        assert mapping["credential_id"] == _CREDENTIAL_ID
        assert mapping["channel"] == "pwa"
        assert mapping["user_agent"] == "AlfredPWA/1.0"
        assert mapping["ip"] == "testclient"
        # Task 11 subtracts this from an aware "now" — a naive stamp would TypeError.
        created = datetime.fromisoformat(mapping["created_at"])
        assert created.utcoffset() == timedelta(0)
        # The TTL must land on the hash it belongs to, not a near-miss key.
        assert redis_mock.expire.await_args.args[0] == key
        assert set(mapping) == {
            "authenticated",
            "credential_id",
            "created_at",
            "ip",
            "user_agent",
            "channel",
        }
        assert "alfred_auth=" in resp.headers["set-cookie"]

    @pytest.mark.asyncio
    async def test_unknown_missing_or_non_string_channel_is_web(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        await _register_test_passkey(store)

        resp = _passkey_login(client, redis_mock, body_extra={"_channel": "toaster"})
        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["channel"] == "web"

        resp = _passkey_login(client, redis_mock)
        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["channel"] == "web"

        # A JSON array is unhashable: an unguarded `in` lookup 500s *after* the sign
        # count was bumped, leaving the caller with a spent assertion and no session.
        resp = _passkey_login(client, redis_mock, body_extra={"_channel": ["pwa"]})
        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["channel"] == "web"

    @pytest.mark.asyncio
    async def test_user_agent_is_truncated_to_200_chars(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        """A hostile client must not park an unbounded string in the session hash."""
        await _register_test_passkey(store)

        resp = _passkey_login(client, redis_mock, headers={"user-agent": "U" * 5000})

        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["user_agent"] == "U" * 200

    @pytest.mark.asyncio
    async def test_missing_peer_records_empty_ip(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """No peer address on the scope → empty ip, never an AttributeError."""
        await _register_test_passkey(store)
        client = _build_client(store, redis_mock, _CHALLENGE, _NoPeerApp)

        resp = _passkey_login(client, redis_mock)

        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["ip"] == ""

    @pytest.mark.asyncio
    async def test_ip_is_the_proxied_peer_behind_a_trusted_proxy(
        self, store: CredentialStore, redis_mock: AsyncMock
    ) -> None:
        """Behind a trusted proxy uvicorn has already rewritten the peer — record that."""
        await _register_test_passkey(store)
        client = _build_client(store, redis_mock, _CHALLENGE, _behind_proxy)

        resp = _passkey_login(client, redis_mock, headers={"X-Forwarded-For": "192.0.2.10"})

        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["ip"] == "192.0.2.10"

    @pytest.mark.asyncio
    async def test_untrusted_caller_cannot_pick_its_own_ip(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        """The peer, never the header — an untrusted caller cannot pick its own ip."""
        await _register_test_passkey(store)

        resp = _passkey_login(client, redis_mock, headers={"X-Forwarded-For": "198.51.100.7"})

        assert resp.status_code == 200
        assert redis_mock.hset.call_args.kwargs["mapping"]["ip"] == "testclient"

    @pytest.mark.asyncio
    async def test_registration_records_channel_too(
        self, store: CredentialStore, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        redis_mock.get = AsyncMock(return_value=bytes_to_base64url(_CHALLENGE).encode())
        verification = MagicMock()
        verification.credential_id = b"\x01\x02\x03"
        verification.credential_public_key = b"\x03"
        verification.sign_count = 0
        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = client.post(
                "/api/auth/register/complete",
                json={**_REGISTER_BODY, "_channel": "ios"},
                headers={"user-agent": "AlfredApp/2.0"},
            )

        assert resp.status_code == 200
        mapping = redis_mock.hset.call_args.kwargs["mapping"]
        assert mapping["channel"] == "ios"
        assert mapping["user_agent"] == "AlfredApp/2.0"
        assert mapping["ip"] == "testclient"


class TestSessionsApi:
    @pytest.fixture
    def sessions(self, redis_mock: AsyncMock) -> dict[str, dict[bytes, bytes]]:
        return _install_sessions(
            redis_mock,
            {
                f"{AUTH_SESSION_PREFIX}s-current": _session_record(
                    _CREDENTIAL_ID, "pwa", "2026-09-04T08:00:00+00:00"
                ),
                f"{AUTH_SESSION_PREFIX}s-older": _session_record(
                    "other-cred", "web", "2026-09-03T08:00:00+00:00"
                ),
                # Pre-upgrade shape: signed in before Task 10 recorded metadata.
                f"{AUTH_SESSION_PREFIX}s-legacy": {
                    b"authenticated": b"1",
                    b"credential_id": _CREDENTIAL_ID.encode(),
                },
                f"{AUTH_SESSION_PREFIX}s-pending": {b"authenticated": b"0"},
            },
        )

    @pytest.mark.asyncio
    async def test_list_sessions_newest_first_with_current_marked(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/sessions")

        assert resp.status_code == 200
        body = resp.json()["sessions"]
        # newest first, stamp-less last; the unauthenticated record is skipped
        assert [s["session_id"] for s in body] == ["s-current", "s-older", "s-legacy"]
        assert body[0] == {
            "session_id": "s-current",
            "credential_id": _CREDENTIAL_ID,
            "device_name": "Phone",
            "channel": "pwa",
            "ip": "203.0.113.7",
            "user_agent": "AlfredPWA/1.0",
            "created_at": "2026-09-04T08:00:00+00:00",
            "expires_in": 1200,
            "current": True,
        }
        assert body[1]["device_name"] == "Unknown device"
        assert body[1]["current"] is False
        # The house SCAN pattern — an unbounded scan stalls the event loop on a
        # keyspace with many sessions.
        redis_mock.scan_iter.assert_called_with(match=f"{AUTH_SESSION_PREFIX}*", count=100)

    def test_current_follows_the_cookie_not_the_ordering(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """``current`` is the cookie's session — not the newest row, nor the first."""
        client.cookies.set("alfred_auth", "s-older")

        body = client.get("/api/auth/sessions").json()["sessions"]

        assert [s["session_id"] for s in body] == ["s-current", "s-older", "s-legacy"]
        assert [s["current"] for s in body] == [False, True, False]

    def test_pre_upgrade_session_falls_back_and_sorts_last(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """A session written before Task 10 has no ip/user_agent/channel/created_at."""
        client.cookies.set("alfred_auth", "s-current")

        body = client.get("/api/auth/sessions").json()["sessions"]

        assert body[-1] == {
            "session_id": "s-legacy",
            "credential_id": _CREDENTIAL_ID,
            "device_name": "Unknown device",
            "channel": "web",
            "ip": "",
            "user_agent": "",
            "created_at": "",
            "expires_in": 1200,
            "current": False,
        }

    def test_unreadable_created_at_never_sorts_newest(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """Garbage in the stamp sorts oldest — a raw string sort would float 'zzz' first."""
        sessions[f"{AUTH_SESSION_PREFIX}s-garbage"] = _session_record(
            "other-cred", "web", "zzz-not-a-date"
        )
        client.cookies.set("alfred_auth", "s-current")

        body = client.get("/api/auth/sessions").json()["sessions"]

        assert body[0]["session_id"] == "s-current"
        assert {s["session_id"] for s in body[-2:]} == {"s-legacy", "s-garbage"}

    @pytest.mark.parametrize("ttl", [-1, -2, None, b"nope"])
    def test_non_positive_or_junk_ttl_reports_zero(
        self,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
        ttl: object,
    ) -> None:
        """A persistent key, a vanished key or a junk TTL is 0 seconds, never a 500."""
        redis_mock.ttl = AsyncMock(return_value=ttl)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/sessions")

        assert resp.status_code == 200
        assert [s["expires_in"] for s in resp.json()["sessions"]] == [0, 0, 0]

    def test_list_is_503_when_the_session_store_is_down(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """A Redis outage mid-scan is a 503, not an unhandled 500."""
        redis_mock.scan_iter = MagicMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/sessions")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    def test_sessions_require_an_authenticated_cookie(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        assert client.get("/api/auth/sessions").status_code == 401
        assert client.delete("/api/auth/sessions/s-older").status_code == 401
        client.cookies.set("alfred_auth", "s-pending")
        assert client.get("/api/auth/sessions").status_code == 401

    def test_delete_another_session(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-older")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-older")
        assert "set-cookie" not in resp.headers

    def test_delete_unknown_session_reports_not_deleted(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """Redis deleting nothing must surface as ``deleted: false``, not a blanket true."""
        redis_mock.delete = AsyncMock(return_value=0)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-gone")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": False}

    def test_delete_accepts_a_session_id_at_the_length_cap(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """128 is the accept edge — the reject case below sits exactly one past it."""
        session_id = "s" * 128
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/sessions/{session_id}")

        assert resp.status_code == 200
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}{session_id}")

    @pytest.mark.parametrize("bad_id", ["%00", "a%00b", "x" * 129, "s older", "s.older"])
    def test_delete_rejects_a_malformed_session_id(
        self,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
        bad_id: str,
    ) -> None:
        """A NUL byte or an overlong id is refused before it reaches Redis or the log."""
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/sessions/{bad_id}")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid session id"
        redis_mock.delete.assert_not_awaited()

    def test_delete_own_session_clears_cookie(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-current")

        assert resp.status_code == 200
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        # Parsed, not substring-matched: Path=/api contains "Path=/" too, and a
        # cookie scoped to the wrong path adds a second one instead of replacing
        # the live session cookie (RFC 6265bis §5.6 identity is name+domain+path).
        morsel = SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]
        assert morsel["path"] == "/"
        assert morsel["max-age"] == "0"
        assert morsel["samesite"].lower() == "strict"
        assert morsel["httponly"]

    def test_logout_all_ends_every_authenticated_session(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout?all=1")

        assert resp.status_code == 200
        deleted = {call.args[0] for call in redis_mock.delete.await_args_list}
        assert deleted == {
            f"{AUTH_SESSION_PREFIX}s-current",
            f"{AUTH_SESSION_PREFIX}s-older",
            f"{AUTH_SESSION_PREFIX}s-legacy",
        }
        assert "Max-Age=0" in resp.headers["set-cookie"]

    @pytest.mark.parametrize("query", ["?all", "?all=maybe", "?all=0", "?all=", ""])
    def test_an_unrecognised_all_flag_still_logs_this_session_out(
        self,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
        query: str,
    ) -> None:
        """A bare or unknown ``all`` must never 422 — that would strand the caller
        with a live session and no way to end it."""
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post(f"/api/auth/logout{query}")

        assert resp.status_code == 200
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert "Max-Age=0" in resp.headers["set-cookie"]

    @pytest.mark.parametrize("query", ["?all=1", "?all=true", "?all=YES"])
    def test_every_truthy_all_spelling_sweeps(
        self,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
        query: str,
    ) -> None:
        client.cookies.set("alfred_auth", "s-current")

        client.post(f"/api/auth/logout{query}")

        deleted = {call.args[0] for call in redis_mock.delete.await_args_list}
        assert f"{AUTH_SESSION_PREFIX}s-older" in deleted

    def test_logout_all_ends_this_session_even_if_the_sweep_fails(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """A Redis failure mid-sweep must not leave the caller signed in with no
        working logout: own key first, cookie cleared, 503 to say the rest is unknown."""
        redis_mock.scan_iter = MagicMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout?all=1")

        assert resp.status_code == 503
        # A bare {"detail": ...}, as every other error in this router spells it.
        assert resp.json() == {"detail": "Session store unavailable"}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert "Max-Age=0" in resp.headers["set-cookie"]

    def test_logout_all_still_ends_this_session_when_the_read_fails(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """A failed read must not skip the delete: clearing the cookie while the
        hash lives on leaves a valid session for the rest of its 8 hours. The sweep
        is skipped (the caller is unproven), so the 503 says so."""
        redis_mock.hgetall = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout?all=1")

        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}
        assert "Max-Age=0" in resp.headers["set-cookie"]

    def test_plain_logout_never_reads_the_session_at_all(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """Only the sweep needs the record. A plain logout that deleted the key
        succeeded — reporting 503 off an unread hash would be a lie."""
        redis_mock.hgetall = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        redis_mock.hgetall.assert_not_awaited()
        assert "Max-Age=0" in resp.headers["set-cookie"]

    def test_routes_are_503_when_the_auth_lookup_is_down(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """An outage in the session lookup is 503, never a 500 — and never a 401,
        which would tell a signed-in caller they are signed out."""
        redis_mock.hgetall = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        for resp in (
            client.get("/api/auth/sessions"),
            client.delete("/api/auth/sessions/s-older"),
        ):
            assert resp.status_code == 503
            assert resp.json()["detail"] == "Session store unavailable"

    def test_delete_is_503_when_the_delete_fails(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        redis_mock.delete = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/sessions/s-older")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    def test_list_is_503_when_the_credential_store_is_down(
        self, store: CredentialStore, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """Device names come from sqlite, not Redis — that outage is a 503 too."""
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(
            store, "list_credentials", AsyncMock(side_effect=OSError("database is locked"))
        ):
            resp = client.get("/api/auth/sessions")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    def test_logout_all_needs_an_authenticated_cookie(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        client.cookies.set("alfred_auth", "s-pending")

        client.post("/api/auth/logout?all=1")

        # Only its own (unauthenticated) key is touched — never everyone else's
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-pending")


class TestPasskeysApi:
    """``GET /api/auth/credentials`` and ``DELETE /api/auth/credentials/{id}``."""

    @pytest.fixture
    def sessions(self, redis_mock: AsyncMock) -> dict[str, dict[bytes, bytes]]:
        return _install_sessions(
            redis_mock,
            {
                f"{AUTH_SESSION_PREFIX}s-current": _session_record(
                    _CREDENTIAL_ID, "pwa", "2026-09-04T08:00:00+00:00"
                ),
                f"{AUTH_SESSION_PREFIX}s-laptop": _session_record(
                    _LAPTOP_CREDENTIAL_ID, "web", "2026-09-03T08:00:00+00:00"
                ),
                # Pre-upgrade shape: signed in before Task 10 recorded a credential_id.
                f"{AUTH_SESSION_PREFIX}s-legacy": {b"authenticated": b"1"},
                f"{AUTH_SESSION_PREFIX}s-pending": {b"authenticated": b"0"},
            },
        )

    @pytest.mark.asyncio
    async def test_list_credentials_marks_current(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.get("/api/auth/credentials")

        assert resp.status_code == 200
        creds = {c["credential_id"]: c for c in resp.json()["credentials"]}
        assert set(creds) == {_CREDENTIAL_ID, _LAPTOP_CREDENTIAL_ID}
        assert creds[_CREDENTIAL_ID]["device_name"] == "Phone"
        assert creds[_CREDENTIAL_ID]["transports"] == ["internal"]
        assert creds[_CREDENTIAL_ID]["current"] is True
        assert creds[_LAPTOP_CREDENTIAL_ID]["current"] is False
        # No public_key and no sign_count: the sheet never needs either, and the
        # key is the one secret in the row.
        assert set(creds[_CREDENTIAL_ID]) == {
            "credential_id",
            "device_name",
            "transports",
            "created_at",
            "last_used_at",
            "current",
        }

    @pytest.mark.asyncio
    async def test_a_session_with_no_credential_marks_nothing_current(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """A pre-upgrade session records no credential_id — an empty match must
        not mark every passkey (or an empty-id one) as the current passkey."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-legacy")

        resp = client.get("/api/auth/credentials")

        assert resp.status_code == 200
        assert [c["current"] for c in resp.json()["credentials"]] == [False, False]

    def test_credentials_require_an_authenticated_cookie(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        assert client.get("/api/auth/credentials").status_code == 401
        assert client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}").status_code == 401
        client.cookies.set("alfred_auth", "s-pending")
        assert client.get("/api/auth/credentials").status_code == 401
        assert client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}").status_code == 401

    def test_401_beats_a_malformed_credential_id(
        self, client: TestClient, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """An anonymous caller learns nothing about which ids are well-formed."""
        assert client.delete("/api/auth/credentials/not a cred").status_code == 401

    @pytest.mark.asyncio
    async def test_delete_credential_ends_its_sessions(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 1}
        assert await store.get_credential(_LAPTOP_CREDENTIAL_ID) is None
        assert await store.get_credential(_CREDENTIAL_ID) is not None
        # Only that passkey's sessions — the caller's own is left signed in.
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-laptop")
        assert "set-cookie" not in resp.headers

    @pytest.mark.asyncio
    async def test_delete_own_credential_clears_cookie(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 1}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        # Parsed, not substring-matched: a cookie scoped to the wrong path adds a
        # second one instead of replacing the live session cookie.
        morsel = SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]
        assert morsel["path"] == "/"
        assert morsel["max-age"] == "0"
        assert morsel["samesite"].lower() == "strict"
        assert morsel["httponly"]

    @pytest.mark.asyncio
    async def test_cannot_delete_last_credential(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot remove the last passkey — register another first"
        assert await store.get_credential(_CREDENTIAL_ID) is not None
        # The refusal is total: no session was ended on the way to it.
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_unknown_credential_is_404(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/credentials/nope")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Passkey not found"
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_accepts_a_credential_id_at_the_length_cap(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """1364 characters is CTAP2's 1023-byte ceiling once base64url-encoded, and the
        accept edge: it reaches the store (404, not 400) where 1365 is refused below."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{'c' * 1364}")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Passkey not found"

    @pytest.mark.parametrize("bad_id", ["%00", "a%00b", "x" * 1365, "cred id", "cred.id", "AQ=="])
    def test_delete_rejects_a_malformed_credential_id(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
        bad_id: str,
    ) -> None:
        """Outside the base64url alphabet, or overlong: refused before the store
        or the log line ever sees it."""
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(store, "list_credentials", AsyncMock(return_value=[])) as listed:
            resp = client.delete(f"/api/auth/credentials/{bad_id}")

        assert resp.status_code == 400
        assert resp.json()["detail"] == "Invalid credential id"
        listed.assert_not_awaited()
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_list_is_503_when_the_credential_store_is_down(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(
            store, "list_credentials", AsyncMock(side_effect=OSError("database is locked"))
        ):
            resp = client.get("/api/auth/credentials")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    @pytest.mark.asyncio
    async def test_delete_is_503_when_the_credential_store_cannot_be_read(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """Without the list there is no telling whether this is the last passkey."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(
            store, "list_credentials", AsyncMock(side_effect=OSError("database is locked"))
        ):
            resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_session_sweep_keeps_the_passkey(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """Sessions are ended first: an outage there must leave the passkey in
        place, never a live session for a credential that is already gone."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        redis_mock.scan_iter = MagicMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"
        assert await store.get_credential(_LAPTOP_CREDENTIAL_ID) is not None

    @pytest.mark.asyncio
    async def test_a_failed_session_delete_keeps_the_passkey(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        redis_mock.delete = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"
        assert await store.get_credential(_LAPTOP_CREDENTIAL_ID) is not None

    @pytest.mark.asyncio
    async def test_delete_is_503_when_the_passkey_cannot_be_removed(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """The sessions are gone by then, so the caller must be told the removal
        itself did not land rather than shown a cheerful 200."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(
            store, "delete_credential", AsyncMock(side_effect=OSError("database is locked"))
        ):
            resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}
        # The sessions really are gone by then — that is what the cookie rule below
        # is about. This caller's own is not among them, so nothing to clear.
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-laptop")
        assert "set-cookie" not in resp.headers

    @pytest.mark.asyncio
    async def test_routes_are_503_when_the_auth_lookup_is_down(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """An outage in the session lookup is 503, never a 500 — and never a 401,
        which would tell a signed-in caller they are signed out."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        redis_mock.hgetall = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        for resp in (
            client.get("/api/auth/credentials"),
            client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}"),
        ):
            assert resp.status_code == 503
            assert resp.json()["detail"] == "Session store unavailable"

    @pytest.mark.asyncio
    async def test_unknown_passkey_is_404_even_when_it_is_the_last_one(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """404 is settled before the last-passkey 409: an id that was never there
        is not the passkey the 409 is protecting."""
        await _register_test_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete("/api/auth/credentials/nope")

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Passkey not found"
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_every_session_of_the_passkey_is_ended(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """A passkey signed in on two devices ends both, not just the first found."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        sessions[f"{AUTH_SESSION_PREFIX}s-laptop-2"] = _session_record(
            _LAPTOP_CREDENTIAL_ID, "ios", "2026-09-02T08:00:00+00:00"
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_LAPTOP_CREDENTIAL_ID}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 2}
        assert {call.args[0] for call in redis_mock.delete.await_args_list} == {
            f"{AUTH_SESSION_PREFIX}s-laptop",
            f"{AUTH_SESSION_PREFIX}s-laptop-2",
        }
        assert "set-cookie" not in resp.headers

    @pytest.mark.asyncio
    async def test_the_cookie_is_cleared_even_when_another_session_follows(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """The caller's own session is not the last one the sweep touches — a
        flag that took the last iteration's answer would leave the cookie live."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        sessions[f"{AUTH_SESSION_PREFIX}s-phone-2"] = _session_record(
            _CREDENTIAL_ID, "ios", "2026-09-02T08:00:00+00:00"
        )
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 200
        assert resp.json() == {"deleted": True, "sessions_ended": 2}
        assert SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_a_failed_removal_clears_the_cookie_it_already_invalidated(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """The sweep ended the caller's own session before the removal failed. A
        503 that kept the cookie would strand the PWA on a session id that is gone."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(
            store, "delete_credential", AsyncMock(side_effect=OSError("database is locked"))
        ):
            resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}
        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        morsel = SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]
        assert morsel["path"] == "/"
        assert morsel["max-age"] == "0"
        assert morsel["samesite"].lower() == "strict"
        assert morsel["httponly"]

    @pytest.mark.asyncio
    async def test_a_failed_sweep_clears_the_cookie_for_the_session_it_did_end(
        self,
        store: CredentialStore,
        client: TestClient,
        redis_mock: AsyncMock,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """Redis dies partway through the sweep, after the caller's own session
        went: the passkey stays, the dead cookie still goes."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        sessions[f"{AUTH_SESSION_PREFIX}s-phone-2"] = _session_record(
            _CREDENTIAL_ID, "ios", "2026-09-02T08:00:00+00:00"
        )
        redis_mock.delete = AsyncMock(side_effect=[1, ConnectionError("redis is down")])
        client.cookies.set("alfred_auth", "s-current")

        resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}
        assert await store.get_credential(_CREDENTIAL_ID) is not None
        assert SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]["max-age"] == "0"

    @pytest.mark.asyncio
    async def test_a_lost_race_on_the_last_passkey_is_409(
        self,
        store: CredentialStore,
        client: TestClient,
        sessions: dict[str, dict[bytes, bytes]],
    ) -> None:
        """The store is the arbiter: if the atomic delete refuses because this
        would have been the last passkey, the route says so rather than 200."""
        await _register_test_passkey(store)
        await _register_laptop_passkey(store)
        client.cookies.set("alfred_auth", "s-current")

        with patch.object(store, "delete_credential", AsyncMock(return_value=0)):
            resp = client.delete(f"/api/auth/credentials/{_CREDENTIAL_ID}")

        assert resp.status_code == 409
        assert resp.json()["detail"] == "Cannot remove the last passkey — register another first"
        # Its sessions went first, so the caller's cookie is stale either way.
        assert SimpleCookie(resp.headers["set-cookie"])["alfred_auth"]["max-age"] == "0"


class TestPairingCode:
    """A signed-in device mints a 6-digit code; a new device registers with it from
    any network. The code lives 5 minutes and is single-use; wrong guesses are budgeted
    per client address, so a stranger's ten misses lock out the stranger, not the code."""

    @pytest.fixture
    def untrusted(self, store: CredentialStore, redis_mock: AsyncMock) -> TestClient:
        return _client_with_gate(store, redis_mock, _reject_network, peer=_DEVICE_A)

    @pytest.fixture
    def untrusted_b(self, store: CredentialStore, redis_mock: AsyncMock) -> TestClient:
        """A second off-LAN device, sharing the same Redis fake as ``untrusted``."""
        return _client_with_gate(store, redis_mock, _reject_network, peer=_DEVICE_B)

    @pytest.fixture
    def active_code(self, redis_mock: AsyncMock) -> str:
        return _serve_pairing_code(redis_mock)

    def test_minting_requires_auth(self, client: TestClient, redis_mock: AsyncMock) -> None:
        """The 401 lands before Redis is touched — an anonymous caller cannot even
        overwrite the active code, which would be a free denial of pairing."""
        resp = client.post("/api/auth/pairing")

        assert resp.status_code == 401
        redis_mock.set.assert_not_awaited()
        redis_mock.delete.assert_not_awaited()

    def test_mint_stores_a_six_digit_code_for_five_minutes(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        redis_mock.hgetall = AsyncMock(
            return_value={b"authenticated": b"1", b"credential_id": b"AQID"}
        )
        client.cookies.set("alfred_auth", "s-current")
        minted_before = datetime.now(UTC)

        resp = client.post("/api/auth/pairing")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body["code"]) == 6 and body["code"].isdigit()
        assert body["ttl_seconds"] == _PAIRING_TTL_SECONDS
        # The stamp is what the PWA counts down against, so it has to be tz-aware
        # and ttl_seconds in the *future* — the window brackets the request itself.
        expires_at = datetime.fromisoformat(body["expires_at"])
        assert expires_at.tzinfo is not None
        assert (
            minted_before + timedelta(seconds=_PAIRING_TTL_SECONDS)
            <= expires_at
            <= datetime.now(UTC) + timedelta(seconds=_PAIRING_TTL_SECONDS)
        )
        # A plain SET (no NX): minting replaces whatever code was live, and touches
        # nothing else — the guess budgets belong to client addresses, not to the code,
        # so a locked-out address cannot free itself by asking for a re-mint.
        redis_mock.set.assert_awaited_once_with(
            WEBAUTHN_PAIRING_KEY, body["code"], ex=_PAIRING_TTL_SECONDS
        )
        redis_mock.delete.assert_not_awaited()

    def test_mint_is_503_when_the_pairing_store_is_down(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        """The mint may not 500 — the PWA shows "try again", not a crash."""
        redis_mock.hgetall = AsyncMock(
            return_value={b"authenticated": b"1", b"credential_id": b"AQID"}
        )
        redis_mock.set = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/pairing")

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    @pytest.mark.parametrize("stored", [_PAIRING_CODE_BYTES, _PAIRING_CODE])
    def test_valid_code_lets_an_untrusted_network_begin_registration(
        self, untrusted: TestClient, redis_mock: AsyncMock, stored: bytes | str
    ) -> None:
        """The production pool (``decode_responses=False``) returns bytes; a decoded
        pool returns str. The active code must match either way."""
        code = _serve_pairing_code(redis_mock, stored)
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = untrusted.post(
                "/api/auth/register/begin",
                json={"device_name": "Phone"},
                headers={"X-Pairing-Code": code},
            )

        assert resp.status_code == 200
        redis_mock.incr.assert_not_awaited()

    def test_valid_code_survives_whitespace_around_it(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        """A code copied by hand arrives padded — strip before matching, or a
        legitimate device is told its code is wrong."""
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = untrusted.post(
                "/api/auth/register/begin",
                json={"device_name": "Phone"},
                headers={"X-Pairing-Code": f"  {active_code}\t"},
            )

        assert resp.status_code == 200
        redis_mock.incr.assert_not_awaited()

    def test_wrong_code_is_rejected_and_counted(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str
    ) -> None:
        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": "000000"},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        # The counter is keyed on the guesser's address, and carries the code's own TTL
        # so a spent budget is released five minutes later rather than held forever.
        assert _pairing_fails_key(_DEVICE_A) == f"alfred:webauthn:pairing:fails:{_DEVICE_A}"
        redis_mock.incr.assert_awaited_once_with(_pairing_fails_key(_DEVICE_A))
        redis_mock.expire.assert_awaited_once_with(
            _pairing_fails_key(_DEVICE_A), _PAIRING_TTL_SECONDS
        )
        redis_mock.delete.assert_not_awaited()

    @pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456", "12345a", "-12345"])
    def test_malformed_code_is_refused_without_counting(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str, code: str
    ) -> None:
        """Anything that is not exactly six digits can never equal the active code,
        so it is refused before Redis is touched. That is static, not a function of
        stored state, so refusing it early leaks nothing — and it keeps junk out of
        an address's budget."""
        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": code},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        redis_mock.get.assert_not_awaited()
        redis_mock.incr.assert_not_awaited()

    def test_nine_misses_leave_the_address_its_last_guess(
        self, untrusted: TestClient, active_code: str
    ) -> None:
        """Ten is the budget, so the ninth miss must not spend it: the same device
        still gets in on the correct code."""
        _spend_guesses(untrusted, 9)

        assert _guess(untrusted, active_code).status_code == 200

    def test_the_tenth_miss_locks_that_address_out_even_with_the_right_code(
        self, untrusted: TestClient, active_code: str, redis_mock: AsyncMock
    ) -> None:
        """Spending the budget refuses the address for the rest of the TTL — that is
        what makes the cap mean anything against a guesser."""
        _spend_guesses(untrusted, 10)

        resp = _guess(untrusted, active_code)

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        # Refused on its own counter, without even reading the code — and the code
        # itself was never deleted.
        assert redis_mock.get.await_args.args[0] == _pairing_fails_key(_DEVICE_A)
        redis_mock.delete.assert_not_awaited()

    def test_an_ipv6_client_cannot_buy_a_fresh_budget_from_its_own_prefix(
        self, store: CredentialStore, redis_mock: AsyncMock, active_code: str
    ) -> None:
        """A residential IPv6 line is handed a whole /64, so keying on the bare address
        would hand one guesser 2**64 budgets and the cap would mean nothing over v6.
        The bucket is the /64: a sibling address inherits the lockout."""
        guesser = _client_with_gate(store, redis_mock, _reject_network, peer=_V6_A)
        sibling = _client_with_gate(store, redis_mock, _reject_network, peer=_V6_A_SIBLING)

        _spend_guesses(guesser, 10)

        assert _guess(sibling, active_code).status_code == 403

    def test_a_different_ipv6_prefix_keeps_its_own_budget(
        self, store: CredentialStore, redis_mock: AsyncMock, active_code: str
    ) -> None:
        """The bucket must not be so wide it re-creates the denial of pairing: another
        subscriber's /64 is a different bucket and still pairs."""
        guesser = _client_with_gate(store, redis_mock, _reject_network, peer=_V6_A)
        elsewhere = _client_with_gate(store, redis_mock, _reject_network, peer=_V6_OTHER_64)

        _spend_guesses(guesser, 10)

        assert _guess(elsewhere, active_code).status_code == 200

    @pytest.mark.parametrize(
        ("peer", "expected"),
        [
            # IPv4 is its own bucket — one address, one budget, unchanged.
            (_DEVICE_A, "alfred:webauthn:pairing:fails:203.0.113.5"),
            (_DEVICE_B, "alfred:webauthn:pairing:fails:198.51.100.9"),
            # IPv6 collapses to the /64 the address sits in, however it is spelled.
            (_V6_A, "alfred:webauthn:pairing:fails:2001:db8::/64"),
            (_V6_A_SIBLING, "alfred:webauthn:pairing:fails:2001:db8::/64"),
            (
                "2001:0db8:0000:0000:0000:0000:0000:0003",  # long form of the same /64
                "alfred:webauthn:pairing:fails:2001:db8::/64",
            ),
            (_V6_OTHER_64, "alfred:webauthn:pairing:fails:2001:db8:0:1::/64"),
            # Not an IP at all: TestClient's peer, or the empty string when the ASGI
            # scope carries no client. Still one bucket, keyed on what we were given.
            ("testclient", "alfred:webauthn:pairing:fails:testclient"),
            ("", "alfred:webauthn:pairing:fails:"),
        ],
    )
    def test_the_fails_key_buckets_the_peer(self, peer: str, expected: str) -> None:
        assert _pairing_fails_key(peer) == expected

    def test_an_unparseable_peer_still_gets_a_budget(
        self, store: CredentialStore, redis_mock: AsyncMock, active_code: str
    ) -> None:
        """A peer that is not an IP — a unix socket, an odd proxy — must not fall out
        of the budget entirely; it just shares one bucket with every other such peer."""
        odd = _client_with_gate(store, redis_mock, _reject_network, peer=None)

        _spend_guesses(odd, 10)

        assert _guess(odd, active_code).status_code == 403
        redis_mock.incr.assert_awaited_with(_pairing_fails_key("testclient"))

    def test_a_locked_out_address_does_not_burn_the_code_for_another_device(
        self, untrusted: TestClient, untrusted_b: TestClient, active_code: str
    ) -> None:
        """The whole point of the per-address budget: ten misses from one address off
        the LAN must not deny pairing to the device that holds the real code — whose
        fallback, the LAN, is exactly what an away device does not have."""
        _spend_guesses(untrusted, 10)

        assert _guess(untrusted, active_code).status_code == 403
        assert _guess(untrusted_b, active_code).status_code == 200

    def test_each_address_gets_its_own_budget(
        self,
        untrusted: TestClient,
        untrusted_b: TestClient,
        active_code: str,
        redis_mock: AsyncMock,
    ) -> None:
        """A second address's misses land on a second key, so they cannot add up to
        a lockout of the first."""
        _spend_guesses(untrusted, 9)
        _spend_guesses(untrusted_b, 9)

        incremented = [call.args[0] for call in redis_mock.incr.await_args_list]
        assert set(incremented) == {
            _pairing_fails_key(_DEVICE_A),
            _pairing_fails_key(_DEVICE_B),
        }
        assert _guess(untrusted, active_code).status_code == 200
        assert _guess(untrusted_b, active_code).status_code == 200

    @pytest.mark.parametrize("failing", ["get", "incr"])
    def test_the_gate_is_503_when_the_pairing_store_is_down(
        self, untrusted: TestClient, redis_mock: AsyncMock, active_code: str, failing: str
    ) -> None:
        """An outage while checking the code is 503, never a 500 — and never the
        403 that would tell a device with a good code to give up."""
        setattr(redis_mock, failing, AsyncMock(side_effect=ConnectionError("redis is down")))

        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": "000000"},
        )

        assert resp.status_code == 503
        assert resp.json()["detail"] == "Session store unavailable"

    def test_no_code_off_network_still_hits_the_network_gate(
        self, untrusted: TestClient, redis_mock: AsyncMock
    ) -> None:
        resp = untrusted.post("/api/auth/register/begin", json={"device_name": "Phone"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Access restricted to trusted networks"

    @pytest.mark.asyncio
    async def test_code_is_consumed_when_the_passkey_is_saved(
        self, untrusted: TestClient, redis_mock: AsyncMock, store: CredentialStore
    ) -> None:
        code = _serve_pairing_code(redis_mock, challenge=b"AQID")
        verification = _register_complete_verification()

        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = untrusted.post(
                "/api/auth/register/complete",
                json=_REGISTER_BODY,
                headers={"X-Pairing-Code": code},
            )

        assert resp.status_code == 200
        deleted = [call.args[0] for call in redis_mock.delete.await_args_list]
        # The code only: the guess counters are per address, not enumerable, and expire
        # on their own TTL.
        assert deleted == [f"{WEBAUTHN_CHALLENGE_PREFIX}c1", WEBAUTHN_PAIRING_KEY]
        assert await store.get_credential(_CREDENTIAL_ID) is not None

    @pytest.mark.asyncio
    async def test_a_failed_consume_keeps_the_registration(
        self, untrusted: TestClient, redis_mock: AsyncMock, store: CredentialStore
    ) -> None:
        """Redis dies after the passkey is saved: the device is registered and gets
        its session. Undoing that would strand a real passkey with no way in."""
        code = _serve_pairing_code(redis_mock, challenge=b"AQID")
        redis_mock.delete = AsyncMock(side_effect=[1, ConnectionError("redis is down")])
        verification = _register_complete_verification()

        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = untrusted.post(
                "/api/auth/register/complete",
                json=_REGISTER_BODY,
                headers={"X-Pairing-Code": code},
            )

        assert resp.status_code == 200
        assert SimpleCookie(resp.headers["set-cookie"])["alfred_auth"].value
        assert await store.get_credential(_CREDENTIAL_ID) is not None

    @pytest.mark.asyncio
    async def test_a_wrong_code_on_complete_saves_no_passkey(
        self, untrusted: TestClient, redis_mock: AsyncMock, store: CredentialStore, active_code: str
    ) -> None:
        """The gate runs before the body is read, so a miss on ``complete`` costs
        the attacker a guess and nothing else."""
        with patch("core.identity.auth_routes.verify_registration_response") as verify:
            resp = untrusted.post(
                "/api/auth/register/complete",
                json=_REGISTER_BODY,
                headers={"X-Pairing-Code": "000000"},
            )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        verify.assert_not_called()
        assert await store.get_credential(_CREDENTIAL_ID) is None

    def test_trusted_network_registration_does_not_touch_pairing_keys(
        self, client: TestClient, redis_mock: AsyncMock
    ) -> None:
        """No header, LAN caller: byte-identical to the pre-pairing behaviour."""
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = client.post("/api/auth/register/begin", json={"device_name": "Phone"})

        assert resp.status_code == 200
        redis_mock.get.assert_not_awaited()
        redis_mock.incr.assert_not_awaited()

    @pytest.mark.parametrize("header", ["", "   ", "\t"])
    def test_an_empty_pairing_header_reads_as_no_header_at_all(
        self, client: TestClient, redis_mock: AsyncMock, header: str
    ) -> None:
        """The PWA fetch spells the optional header ``code ?? ""``. An empty value
        means "no code", not "wrong code" — 403ing it would lock the LAN flow out
        of registration entirely, so it falls through to the network gate."""
        gen, to_json = _registration_options_patched()
        with gen, to_json:
            resp = client.post(
                "/api/auth/register/begin",
                json={"device_name": "Phone"},
                headers={"X-Pairing-Code": header},
            )

        assert resp.status_code == 200
        redis_mock.get.assert_not_awaited()
        redis_mock.incr.assert_not_awaited()

    @pytest.mark.parametrize("header", ["", "   "])
    def test_an_empty_pairing_header_off_the_lan_is_the_network_refusal(
        self, untrusted: TestClient, redis_mock: AsyncMock, header: str
    ) -> None:
        """Falling through means the network gate answers — with its own wording,
        not the pairing one, so the device is told what is actually wrong."""
        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": header},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Access restricted to trusted networks"
        redis_mock.get.assert_not_awaited()
        redis_mock.incr.assert_not_awaited()

    def test_a_guess_with_no_code_live_costs_the_same_work_as_one_with(
        self, untrusted: TestClient, redis_mock: AsyncMock
    ) -> None:
        """Uniform work is the point: a well-formed guess does the same reads and the
        same INCR+EXPIRE whether or not a code is live, so the round trips no longer
        tell a caller which it is. Counting it is harmless now that the budget is the
        guesser's own."""
        _serve_pairing_code(redis_mock, stored=b"")  # nothing live to guess at

        resp = untrusted.post(
            "/api/auth/register/begin",
            json={"device_name": "Phone"},
            headers={"X-Pairing-Code": _PAIRING_CODE},
        )

        assert resp.status_code == 403
        assert resp.json()["detail"] == "Invalid or expired pairing code"
        assert [c.args[0] for c in redis_mock.get.await_args_list] == [
            _pairing_fails_key(_DEVICE_A),
            WEBAUTHN_PAIRING_KEY,
        ]
        redis_mock.incr.assert_awaited_once_with(_pairing_fails_key(_DEVICE_A))
        redis_mock.expire.assert_awaited_once_with(
            _pairing_fails_key(_DEVICE_A), _PAIRING_TTL_SECONDS
        )

    @pytest.mark.asyncio
    async def test_trusted_network_completion_does_not_touch_pairing_keys(
        self, client: TestClient, redis_mock: AsyncMock, store: CredentialStore
    ) -> None:
        """A LAN enrolment consumes nothing: the challenge is the only key deleted,
        and the pairing keys are never even read."""
        redis_mock.get = AsyncMock(return_value=b"AQID")
        verification = _register_complete_verification()

        with patch(
            "core.identity.auth_routes.verify_registration_response", return_value=verification
        ):
            resp = client.post("/api/auth/register/complete", json=_REGISTER_BODY)

        assert resp.status_code == 200
        assert [c.args[0] for c in redis_mock.delete.await_args_list] == [
            f"{WEBAUTHN_CHALLENGE_PREFIX}c1"
        ]
        # ``get`` is awaited once, for the challenge — never for the pairing key.
        assert [c.args[0] for c in redis_mock.get.await_args_list] == [
            f"{WEBAUTHN_CHALLENGE_PREFIX}c1"
        ]
        redis_mock.incr.assert_not_awaited()
        assert await store.get_credential(_CREDENTIAL_ID) is not None
