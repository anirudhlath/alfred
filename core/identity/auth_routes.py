"""WebAuthn registration and authentication endpoints."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, NamedTuple

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from loguru import logger
from pydantic import BaseModel, Field
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from shared.env import is_truthy_flag
from shared.streams import (
    AUTH_SESSION_PREFIX,
    WEBAUTHN_CHALLENGE_PREFIX,
    decode_stream_value,
)

if TYPE_CHECKING:
    from core.identity.credentials import CredentialStore

_AUTH_SESSION_TTL = 8 * 3600  # 8 hours — a phone re-auths with Face ID, cheap to renew
_CHALLENGE_TTL = 300  # 5 minutes
_MAX_USER_AGENT_LEN = 200  # bounds what a hostile client can park in the session hash
# Session ids are uuid4 strings; the class also covers a token_urlsafe id should
# the generator ever change. Anything else is refused before it reaches Redis or
# the log line, so a %00 or a 5000-character path segment goes nowhere.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")
# Credential ids are unpadded base64url (``bytes_to_base64url``), so the same
# alphabet with CTAP2's 1023-byte ceiling on a credential id — 1364 characters
# once encoded. Anything else cannot name a stored passkey, so it is refused
# before the store or the log line sees it.
_CREDENTIAL_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,1364}")


class _CurrentSession(NamedTuple):
    """The caller's session: the id from the cookie, and the record behind it.

    Carried together so a route that needs the record (which passkey signed this
    session in) does not re-read a hash the 401 check has already fetched.
    """

    session_id: str
    record: dict[str, str]


class RegisterBeginRequest(BaseModel):
    device_name: str = Field(max_length=100)


def _get_rp_id(request: Request) -> str:
    """Derive rp_id from the request host."""
    host = request.headers.get("host", "localhost")
    return host.split(":")[0]


def _get_origin(request: Request) -> str:
    """Derive origin from the request."""
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", "localhost")
    return f"{scheme}://{host}"


def _to_transports(values: list[str]) -> list[AuthenticatorTransport] | None:
    """Convert stored transport strings to enum members, dropping unknown values."""
    out: list[AuthenticatorTransport] = []
    for v in values:
        try:
            out.append(AuthenticatorTransport(v))
        except ValueError:
            logger.debug("Ignoring unknown WebAuthn transport: {}", v)
    return out or None


# Where an auth session was *started* — deliberately distinct from the message
# channel vocabulary in core/channels/web_server.py (`_CHANNEL_SOURCE_MAP`:
# "web_pwa"/"voice"/"ios"). "pwa" here is the session-origin spelling of that
# map's "web_pwa"; "web" has no counterpart there, and "voice" rides an
# existing session rather than starting one. The wire values are frozen by spec.
_SESSION_CHANNELS = frozenset({"web", "pwa", "ios"})


def _session_channel(body: dict[str, Any]) -> str:
    """Which client completed the ceremony — ``_channel`` in the completion body."""
    channel = body.get("_channel", "web")
    # A JSON array/object would be unhashable — and this runs after the ceremony's
    # side effects, so an exception here means a 500 and a sessionless passkey.
    return channel if isinstance(channel, str) and channel in _SESSION_CHANNELS else "web"


def _set_session_cookie(response: JSONResponse, request: Request, session_id: str) -> None:
    """Attach the HttpOnly session cookie (Secure whenever the request was HTTPS)."""
    response.set_cookie(
        key="alfred_auth",
        value=session_id,
        max_age=_AUTH_SESSION_TTL,
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


def _clear_session_cookie(response: JSONResponse, request: Request) -> None:
    """Expire the session cookie, mirroring the flags it was set with.

    What decides whether this replaces the live cookie rather than adding a second
    one is name + domain + path (RFC 6265bis §5.6) — samesite is not part of that
    identity. ``secure`` still matters: "Leave Secure Cookies Alone" (§5.5) stops a
    plaintext response from clearing a Secure cookie at all. The rest is mirrored
    from ``_set_session_cookie`` so the two spellings never drift.
    """
    response.delete_cookie(
        key="alfred_auth",
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


def _created_at_sort_key(value: str) -> datetime:
    """Sort key for a session's ``created_at`` — unreadable or missing sorts oldest.

    Pre-upgrade sessions carry no stamp at all, and a naive one (written before
    Task 10 made them tz-aware) is read as UTC so the comparison never TypeErrors.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _expires_in(ttl: Any) -> int:
    """Seconds left on a session key: a missing key (-2), a persistent key (-1)
    or anything non-numeric all report 0 rather than 500ing the list."""
    try:
        return max(int(ttl), 0)
    except (TypeError, ValueError):
        return 0


def _decode_session(raw: Any) -> dict[str, str]:
    """Normalise a session hash (bytes or str keys and values) to str → str."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        key = k.decode() if isinstance(k, bytes) else str(k)
        out[key] = v.decode(errors="replace") if isinstance(v, bytes) else str(v)
    return out


def create_auth_router(
    *,
    store: CredentialStore,
    redis: Any,
    trusted_network_dep: Any = None,
) -> APIRouter:
    """Build the auth APIRouter with all WebAuthn endpoints.

    Args:
        store: WebAuthn credential store.
        redis: Async Redis connection for sessions/challenges.
        trusted_network_dep: FastAPI dependency for trusted network check.
            If None, imports ``require_trusted_network`` from web_server (backwards compat).
    """
    if trusted_network_dep is None:
        from core.channels.web_server import require_trusted_network

        trusted_network_dep = require_trusted_network

    router = APIRouter(prefix="/api/auth", tags=["auth"])

    async def _start_session(request: Request, *, credential_id: str, channel: str) -> str:
        """Create an authenticated session hash (TTL 8h) and return its id."""
        session_id = str(uuid.uuid4())
        key = f"{AUTH_SESSION_PREFIX}{session_id}"
        await redis.hset(
            key,
            mapping={
                "authenticated": "1",
                "credential_id": credential_id,
                "created_at": datetime.now(UTC).isoformat(),
                "ip": request.client.host if request.client else "",
                "user_agent": request.headers.get("user-agent", "")[:_MAX_USER_AGENT_LEN],
                "channel": channel,
            },
        )
        await redis.expire(key, _AUTH_SESSION_TTL)
        return session_id

    async def _current_session(
        alfred_auth: str | None = Cookie(default=None),
    ) -> _CurrentSession:
        """The caller's authenticated session, else 401 — or 503 if Redis is down.

        Reads Redis directly rather than trusting ``request.state`` so the router
        also works without ``AuthCookieMiddleware`` (as in its tests). An outage is
        503 rather than 401: the caller may well be signed in, we just can't tell.
        """
        if not alfred_auth:
            raise HTTPException(status_code=401, detail="Authentication required")
        try:
            record = _decode_session(await redis.hgetall(f"{AUTH_SESSION_PREFIX}{alfred_auth}"))
        except Exception as e:
            logger.warning("Could not read the caller's auth session: {}", e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        if record.get("authenticated") != "1":
            raise HTTPException(status_code=401, detail="Authentication required")
        return _CurrentSession(alfred_auth, record)

    # Held as a name, not called in each default: ruff's B008 exempts an inline
    # ``Depends(...)`` only under a simple annotation, and the session routes are
    # annotated with ``_CurrentSession``.
    _session_dep = Depends(_current_session)

    async def _all_sessions() -> list[tuple[str, dict[str, str]]]:
        """Every live, authenticated session as ``(session_id, record)``."""
        found: list[tuple[str, dict[str, str]]] = []
        # The hgetall per key is a deliberate N+1, as in core/routing/pending.py: a
        # handful of 8-hour sessions at most, so pipelining would be a novel pattern
        # here for no measurable gain.
        async for key in redis.scan_iter(match=f"{AUTH_SESSION_PREFIX}*", count=100):
            session_id = decode_stream_value(key).removeprefix(AUTH_SESSION_PREFIX)
            record = _decode_session(await redis.hgetall(f"{AUTH_SESSION_PREFIX}{session_id}"))
            if record.get("authenticated") == "1":
                found.append((session_id, record))
        return found

    @router.get("/status")
    async def auth_status(request: Request) -> JSONResponse:
        registered = await store.has_any_credential()
        authenticated = getattr(request.state, "authenticated", False)
        return JSONResponse({"registered": registered, "authenticated": authenticated})

    @router.post("/register/begin")
    async def register_begin(
        body: RegisterBeginRequest,
        request: Request,
        _: None = Depends(trusted_network_dep),
    ) -> JSONResponse:
        user_id_hex = await store.get_or_create_user_id()
        user_id_bytes = bytes.fromhex(user_id_hex)

        existing = await store.list_credentials()
        exclude = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c.credential_id),
                transports=_to_transports(c.transports),
            )
            for c in existing
        ]

        rp_id = _get_rp_id(request)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name="Alfred",
            user_name="sir",
            user_id=user_id_bytes,
            user_display_name="Sir",
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.PREFERRED,
                user_verification=UserVerificationRequirement.PREFERRED,
            ),
            exclude_credentials=exclude,
        )

        challenge_id = str(uuid.uuid4())
        await redis.set(
            f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}",
            bytes_to_base64url(options.challenge),
            ex=_CHALLENGE_TTL,
        )

        options_json = json.loads(options_to_json(options))
        options_json["_challenge_id"] = challenge_id
        options_json["_device_name"] = body.device_name
        return JSONResponse(options_json)

    @router.post("/register/complete")
    async def register_complete(
        request: Request,
        _: None = Depends(trusted_network_dep),
    ) -> JSONResponse:
        body = await request.json()
        challenge_id = body.get("_challenge_id", "")
        device_name = body.get("_device_name", "Unknown Device")

        stored_challenge_b64 = await redis.get(f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}")
        if not stored_challenge_b64:
            raise HTTPException(status_code=400, detail="Challenge expired or invalid")
        if isinstance(stored_challenge_b64, bytes):
            stored_challenge_b64 = stored_challenge_b64.decode()

        await redis.delete(f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}")

        expected_challenge = base64url_to_bytes(stored_challenge_b64)
        rp_id = _get_rp_id(request)
        origin = _get_origin(request)

        try:
            verification = verify_registration_response(
                credential=body,
                expected_challenge=expected_challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
            )
        except Exception as e:
            logger.warning("Registration verification failed: {}", e)
            raise HTTPException(status_code=401, detail="Authentication failed") from e

        credential_id = bytes_to_base64url(verification.credential_id)
        await store.save_credential(
            credential_id=credential_id,
            public_key=verification.credential_public_key,
            sign_count=verification.sign_count,
            device_name=device_name,
            transports=body.get("response", {}).get("transports", []),
        )

        session_id = await _start_session(
            request, credential_id=credential_id, channel=_session_channel(body)
        )
        response = JSONResponse({"status": "ok", "credential_id": credential_id})
        _set_session_cookie(response, request, session_id)
        return response

    @router.post("/login/begin")
    async def login_begin(request: Request) -> JSONResponse:
        credentials = await store.list_credentials()
        if not credentials:
            raise HTTPException(status_code=404, detail="No credentials registered")

        allow_credentials = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c.credential_id),
                transports=_to_transports(c.transports),
            )
            for c in credentials
        ]

        rp_id = _get_rp_id(request)
        options = generate_authentication_options(
            rp_id=rp_id,
            allow_credentials=allow_credentials,
            user_verification=UserVerificationRequirement.PREFERRED,
        )

        challenge_id = str(uuid.uuid4())
        await redis.set(
            f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}",
            bytes_to_base64url(options.challenge),
            ex=_CHALLENGE_TTL,
        )

        options_json = json.loads(options_to_json(options))
        options_json["_challenge_id"] = challenge_id
        return JSONResponse(options_json)

    @router.post("/login/complete")
    async def login_complete(request: Request) -> JSONResponse:
        body = await request.json()
        challenge_id = body.get("_challenge_id", "")

        stored_challenge_b64 = await redis.get(f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}")
        if not stored_challenge_b64:
            raise HTTPException(status_code=400, detail="Challenge expired or invalid")
        if isinstance(stored_challenge_b64, bytes):
            stored_challenge_b64 = stored_challenge_b64.decode()

        await redis.delete(f"{WEBAUTHN_CHALLENGE_PREFIX}{challenge_id}")

        credential_id_from_body = body.get("id", "")
        cred = await store.get_credential(credential_id_from_body)
        if not cred:
            raise HTTPException(status_code=401, detail="Authentication failed")

        expected_challenge = base64url_to_bytes(stored_challenge_b64)
        rp_id = _get_rp_id(request)
        origin = _get_origin(request)

        try:
            verification = verify_authentication_response(
                credential=body,
                expected_challenge=expected_challenge,
                expected_rp_id=rp_id,
                expected_origin=origin,
                credential_public_key=cred.public_key,
                credential_current_sign_count=cred.sign_count,
            )
        except Exception as e:
            logger.warning("Authentication verification failed: {}", e)
            raise HTTPException(status_code=401, detail="Authentication failed") from e

        await store.update_sign_count(cred.credential_id, verification.new_sign_count)

        session_id = await _start_session(
            request, credential_id=cred.credential_id, channel=_session_channel(body)
        )
        response = JSONResponse({"status": "ok"})
        _set_session_cookie(response, request, session_id)
        return response

    @router.get("/sessions")
    async def list_sessions(
        current: _CurrentSession = _session_dep,
    ) -> JSONResponse:
        """Every live session, newest first, with the caller's marked ``current``."""
        sessions: list[dict[str, Any]] = []
        try:
            names = {c.credential_id: c.device_name for c in await store.list_credentials()}
            for session_id, record in await _all_sessions():
                ttl = await redis.ttl(f"{AUTH_SESSION_PREFIX}{session_id}")
                credential_id = record.get("credential_id", "")
                sessions.append(
                    {
                        "session_id": session_id,
                        "credential_id": credential_id,
                        "device_name": names.get(credential_id, "Unknown device"),
                        "channel": record.get("channel", "web"),
                        "ip": record.get("ip", ""),
                        "user_agent": record.get("user_agent", ""),
                        "created_at": record.get("created_at", ""),
                        "expires_in": _expires_in(ttl),
                        "current": session_id == current.session_id,
                    }
                )
        except Exception as e:
            # Covers the credential store as well as Redis — either one missing
            # means the list would be wrong rather than merely incomplete.
            logger.warning("Could not list auth sessions: {}", e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        sessions.sort(key=lambda s: _created_at_sort_key(str(s["created_at"])), reverse=True)
        return JSONResponse({"sessions": sessions})

    @router.delete("/sessions/{session_id}")
    async def delete_session(
        session_id: str,
        request: Request,
        current: _CurrentSession = _session_dep,
    ) -> JSONResponse:
        """End one session. Ending your own also clears the cookie."""
        if not _SESSION_ID_RE.fullmatch(session_id):
            raise HTTPException(status_code=400, detail="Invalid session id")
        try:
            deleted = await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
        except Exception as e:
            logger.warning("Could not end auth session {}: {}", session_id, e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        logger.info(
            "Auth session {} ended via the sessions API (deleted={})", session_id, bool(deleted)
        )
        response = JSONResponse({"deleted": bool(deleted)})
        if session_id == current.session_id:
            _clear_session_cookie(response, request)
        return response

    @router.get("/credentials")
    async def list_passkeys(
        current: _CurrentSession = _session_dep,
    ) -> JSONResponse:
        """Every registered passkey, with the one this session signed in with marked.

        Deliberately no ``public_key`` and no ``sign_count``: the sheet needs
        neither, and the key is the one secret on the row.
        """
        try:
            credentials = await store.list_credentials()
        except Exception as e:
            logger.warning("Could not list passkeys: {}", e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        # No default: a pre-upgrade session records no credential_id, and ``None``
        # matches no passkey rather than every one of them.
        current_credential = current.record.get("credential_id")
        return JSONResponse(
            {
                "credentials": [
                    {
                        "credential_id": c.credential_id,
                        "device_name": c.device_name,
                        "transports": c.transports,
                        "created_at": c.created_at,
                        "last_used_at": c.last_used_at,
                        "current": c.credential_id == current_credential,
                    }
                    for c in credentials
                ]
            }
        )

    @router.delete("/credentials/{credential_id}")
    async def delete_passkey(
        credential_id: str,
        request: Request,
        current: _CurrentSession = _session_dep,
    ) -> JSONResponse:
        """Remove a passkey and end every session it opened. Never the last one."""
        if not _CREDENTIAL_ID_RE.fullmatch(credential_id):
            raise HTTPException(status_code=400, detail="Invalid credential id")
        try:
            # One read answers both questions: does it exist, and is it the last
            # one. Without it there is no telling either way, so an outage here
            # must not fall through to a removal.
            existing = await store.list_credentials()
        except Exception as e:
            logger.warning("Could not read the passkeys before removing {}: {}", credential_id, e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        if not any(c.credential_id == credential_id for c in existing):
            raise HTTPException(status_code=404, detail="Passkey not found")
        if len(existing) <= 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot remove the last passkey — register another first",
            )

        ended = 0
        ended_own = False
        try:
            # Sessions first, and only then the credential: a failure between the
            # two leaves a passkey with fewer sessions, which is harmless, rather
            # than a live session for a passkey that no longer exists.
            for session_id, record in await _all_sessions():
                if record.get("credential_id") != credential_id:
                    continue
                await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
                ended += 1
                ended_own = ended_own or session_id == current.session_id
        except Exception as e:
            logger.warning("Could not end the sessions for passkey {}: {}", credential_id, e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e

        try:
            await store.delete_credential(credential_id)
        except Exception as e:
            logger.warning("Could not remove passkey {}: {}", credential_id, e)
            raise HTTPException(status_code=503, detail="Session store unavailable") from e
        logger.info("Passkey {} removed, {} session(s) ended", credential_id, ended)

        response = JSONResponse({"deleted": True, "sessions_ended": ended})
        if ended_own:
            _clear_session_cookie(response, request)
        return response

    @router.post("/logout")
    async def logout(
        request: Request,
        alfred_auth: str | None = Cookie(default=None),
        all_sessions: str = Query(default="", alias="all"),
    ) -> JSONResponse:
        """End the caller's session — or every session with ``?all=1``.

        ``all`` is read as a flag rather than typed as a bool: a bare ``?all`` or
        a spelling Alfred doesn't recognise must still end *this* session instead
        of 422ing, or a client with an odd query string can never log out at all.
        It is only honoured for an authenticated caller, so a guessed cookie value
        can never log the real user out of every device.
        """
        ended = True
        if alfred_auth:
            own_key = f"{AUTH_SESSION_PREFIX}{alfred_auth}"
            # Only the sweep needs the record, so the common logout skips the read
            # entirely — a round-trip saved, and an outage on it can no longer make
            # an ordinary logout that worked report 503. It is guarded separately
            # from the delete either way: skipping the delete would leave the caller
            # cookie-less but still signed in for the rest of the 8-hour TTL.
            record: dict[str, str] = {}
            want_all = is_truthy_flag(all_sessions)
            if want_all:
                try:
                    record = _decode_session(await redis.hgetall(own_key))
                except Exception as e:
                    logger.warning("Logout could not read the caller's session: {}", e)
                    ended = False
            try:
                # The caller's own key goes first: however the sweep below fares,
                # the cookie cleared on the way out must not still name a session.
                await redis.delete(own_key)
                if want_all and record.get("authenticated") == "1":
                    for session_id, _ in await _all_sessions():
                        await redis.delete(f"{AUTH_SESSION_PREFIX}{session_id}")
                    logger.info("All auth sessions ended via logout?all=1")
            except Exception as e:
                logger.warning("Logout could not end every session: {}", e)
                ended = False

        response = JSONResponse(
            {"status": "ok"} if ended else {"detail": "Session store unavailable"},
            status_code=200 if ended else 503,
        )
        _clear_session_cookie(response, request)
        return response

    return router
