"""WebAuthn registration and authentication endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
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
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from shared.streams import AUTH_SESSION_PREFIX, WEBAUTHN_CHALLENGE_PREFIX

if TYPE_CHECKING:
    from core.identity.credentials import CredentialStore

_AUTH_SESSION_TTL = 86400  # 24 hours
_CHALLENGE_TTL = 300  # 5 minutes


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
                transports=c.transports,  # type: ignore[arg-type]
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

        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await redis.hset(
            f"{AUTH_SESSION_PREFIX}{session_id}",
            mapping={
                "authenticated": "1",
                "credential_id": credential_id,
                "created_at": now,
            },
        )
        await redis.expire(f"{AUTH_SESSION_PREFIX}{session_id}", _AUTH_SESSION_TTL)

        response = JSONResponse({"status": "ok", "credential_id": credential_id})
        response.set_cookie(
            key="alfred_auth",
            value=session_id,
            max_age=_AUTH_SESSION_TTL,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @router.post("/login/begin")
    async def login_begin(request: Request) -> JSONResponse:
        credentials = await store.list_credentials()
        if not credentials:
            raise HTTPException(status_code=404, detail="No credentials registered")

        allow_credentials = [
            PublicKeyCredentialDescriptor(
                id=base64url_to_bytes(c.credential_id),
                transports=c.transports,  # type: ignore[arg-type]
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

        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        await redis.hset(
            f"{AUTH_SESSION_PREFIX}{session_id}",
            mapping={
                "authenticated": "1",
                "credential_id": cred.credential_id,
                "created_at": now,
            },
        )
        await redis.expire(f"{AUTH_SESSION_PREFIX}{session_id}", _AUTH_SESSION_TTL)

        response = JSONResponse({"status": "ok"})
        response.set_cookie(
            key="alfred_auth",
            value=session_id,
            max_age=_AUTH_SESSION_TTL,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    @router.post("/logout")
    async def logout(
        request: Request,
        alfred_auth: str | None = Cookie(default=None),
    ) -> JSONResponse:
        if alfred_auth:
            await redis.delete(f"{AUTH_SESSION_PREFIX}{alfred_auth}")

        response = JSONResponse({"status": "ok"})
        response.delete_cookie(key="alfred_auth")
        return response

    return router
