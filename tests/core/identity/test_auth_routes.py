"""Tests for WebAuthn auth routes."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from webauthn.helpers import bytes_to_base64url

from core.identity.auth_routes import create_auth_router
from core.identity.credentials import CredentialStore
from shared.streams import AUTH_SESSION_PREFIX

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

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

_REGISTER_BODY: dict[str, object] = {
    "_challenge_id": "c1",
    "_device_name": "Phone",
    "id": _CREDENTIAL_ID,
    "rawId": _CREDENTIAL_ID,
    "type": "public-key",
    "response": {
        "clientDataJSON": "e30",
        "attestationObject": "e30",
    },
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


def _aiter(items: list[bytes | str]) -> AsyncIterator[bytes | str]:
    async def gen() -> AsyncIterator[bytes | str]:
        for item in items:
            yield item

    return gen()


def _scanned_keys(sessions: dict[str, dict[bytes, bytes]]) -> list[bytes | str]:
    """Keys as SCAN hands them over: bytes on the production pool
    (``decode_responses=False``), with the first left as str so both branches of
    ``_key_suffix`` are exercised."""
    return [key if index == 0 else key.encode() for index, key in enumerate(sessions)]


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


def _reject_network() -> None:
    """Dependency override that always rejects as untrusted."""
    raise HTTPException(status_code=403, detail="Access restricted to trusted networks")


class TestRegistrationBegin:
    def test_returns_options(self, client: TestClient, redis_mock: AsyncMock) -> None:
        with (
            patch("core.identity.auth_routes.generate_registration_options") as mock_gen,
            patch("core.identity.auth_routes.options_to_json") as mock_json,
        ):
            mock_options = MagicMock()
            mock_options.challenge = b"\x01\x02\x03"
            mock_gen.return_value = mock_options
            mock_json.return_value = '{"test": "options"}'

            resp = client.post(
                "/api/auth/register/begin",
                json={"device_name": "MacBook Pro"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["test"] == "options"
            mock_gen.assert_called_once()

    def test_rejects_untrusted_network(self, app: FastAPI) -> None:
        from core.channels.web_server import require_trusted_network

        app.dependency_overrides[require_trusted_network] = _reject_network
        try:
            untrusted_client = TestClient(app)
            resp = untrusted_client.post(
                "/api/auth/register/begin",
                json={"device_name": "Test"},
            )
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(require_trusted_network, None)


class TestRegistrationComplete:
    def test_rejects_untrusted_network(self, app: FastAPI, client: TestClient) -> None:
        from core.channels.web_server import require_trusted_network

        app.dependency_overrides[require_trusted_network] = _reject_network
        try:
            resp = client.post(
                "/api/auth/register/complete",
                json={"credential": "{}"},
            )
            assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(require_trusted_network, None)


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

            # trusted_network_dep=lambda: None bypasses the IP gate in tests
            _app.include_router(
                create_auth_router(
                    store=store,
                    redis=redis_mock,
                    trusted_network_dep=lambda: None,
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
        sessions = {
            f"{AUTH_SESSION_PREFIX}s-current": _session_record(
                _CREDENTIAL_ID, "pwa", "2026-09-04T08:00:00+00:00"
            ),
            f"{AUTH_SESSION_PREFIX}s-older": _session_record(
                "other-cred", "web", "2026-09-03T08:00:00+00:00"
            ),
            # Pre-upgrade shape: signed in before Task 10 started recording metadata.
            f"{AUTH_SESSION_PREFIX}s-legacy": {
                b"authenticated": b"1",
                b"credential_id": _CREDENTIAL_ID.encode(),
            },
            f"{AUTH_SESSION_PREFIX}s-pending": {b"authenticated": b"0"},
        }
        redis_mock.hgetall = AsyncMock(side_effect=lambda key: sessions.get(key, {}))
        # Built per call, so a test can add a session and have the scan see it.
        redis_mock.scan_iter = MagicMock(
            side_effect=lambda match="*", count=100: _aiter(_scanned_keys(sessions))
        )
        redis_mock.ttl = AsyncMock(return_value=1200)
        redis_mock.delete = AsyncMock(return_value=1)
        return sessions

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
        cookie = resp.headers["set-cookie"]
        # Name and path decide whether this replaces the live cookie or merely
        # adds a second one; the remaining flags mirror _set_session_cookie.
        assert "alfred_auth=" in cookie
        assert "Path=/" in cookie
        assert "Max-Age=0" in cookie
        assert "HttpOnly" in cookie
        assert "samesite=strict" in cookie.lower()

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

    def test_logout_still_ends_this_session_when_the_read_fails(
        self, client: TestClient, redis_mock: AsyncMock, sessions: dict[str, dict[bytes, bytes]]
    ) -> None:
        """A failed pre-delete read must not skip the delete: clearing the cookie
        while the hash lives on leaves a valid session for the rest of its 8 hours."""
        redis_mock.hgetall = AsyncMock(side_effect=ConnectionError("redis is down"))
        client.cookies.set("alfred_auth", "s-current")

        resp = client.post("/api/auth/logout")

        redis_mock.delete.assert_awaited_once_with(f"{AUTH_SESSION_PREFIX}s-current")
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Session store unavailable"}
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
