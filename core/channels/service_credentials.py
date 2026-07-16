"""Sovereign-service credential helpers + ServiceRegistered re-push worker.

Sovereign services (home-service, signal-bridge, ...) declare a
``credentials_schema`` and ``credentials_endpoint`` in their SDK registration
manifest (Redis hash ``alfred:tool_registry``). Core is the single credential
authority: fields are stored in the OS keyring (namespace = service name;
secrets never touch Redis or non-keyring disk) and pushed to the service's
``credentials_endpoint`` over the trusted network.

Self-healing: the channels process consumes ``ServiceRegistered`` events from
``alfred:events`` (consumer group ``channels-credentials``) and re-pushes
stored credentials whenever a service (re)registers — services keep
credentials in memory only and recover within one registration cycle after a
restart. Event-driven, no polling.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from core.integrations.base import CredentialSchema
from shared.secrets import aget_all_secrets
from shared.streams import TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    import httpx

    from shared.types import AioRedis

CREDENTIAL_PUSH_GROUP = "channels-credentials"


# ── registry reads ──


def _parse_manifest(name: str, raw: bytes | str) -> dict[str, Any] | None:
    """Decode one registry manifest; None (logged) if malformed or without a usable schema."""
    try:
        manifest: dict[str, Any] = json.loads(decode_stream_value(raw))
    except (TypeError, json.JSONDecodeError):
        logger.error("Invalid JSON in tool registry for service '{}'", name)
        return None
    schema_dict = manifest.get("credentials_schema")
    if not schema_dict:
        return None
    try:
        CredentialSchema.model_validate(schema_dict)
    except ValidationError:
        logger.error("Malformed credentials_schema in registry for service '{}'", name)
        return None
    return manifest


async def list_service_manifests(redis: AioRedis) -> dict[str, dict[str, Any]]:
    """All registry manifests that declare a valid credentials_schema, keyed by service name."""
    raw: dict[bytes | str, bytes | str] = await redis.hgetall(TOOL_REGISTRY_KEY)
    manifests: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        name = key.decode() if isinstance(key, bytes) else key
        manifest = _parse_manifest(name, value)
        if manifest is not None:
            manifests[name] = manifest
    return manifests


async def get_service_manifest(redis: AioRedis, name: str) -> dict[str, Any] | None:
    """One registry manifest; None if absent, malformed, or without a credentials_schema."""
    raw = await redis.hget(TOOL_REGISTRY_KEY, name)
    if raw is None:
        return None
    return _parse_manifest(name, raw)


def parse_schema(manifest: dict[str, Any]) -> CredentialSchema:
    """Parse a manifest's credentials_schema into the core CredentialSchema model."""
    return CredentialSchema.model_validate(manifest["credentials_schema"])


# ── validation (shared by adapter + service PUT paths) ──


def validate_credential_body(schema: CredentialSchema, body: dict[str, str]) -> None:
    """Reject unknown fields and missing required non-transient fields (HTTP 422)."""
    unknown = set(body) - set(schema.fields)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown fields: {unknown}")
    missing = [
        f
        for f, field in schema.fields.items()
        if field.required and f not in body and not field.transient
    ]
    if missing:
        raise HTTPException(status_code=422, detail=f"Missing required fields: {missing}")


# ── GET /api/integrations entry (contract C5) ──


async def build_service_info(name: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Build a merged-integrations entry for a registry-declared service."""
    schema = parse_schema(manifest)
    stored = await aget_all_secrets(name, list(schema.fields))
    return {
        "name": name,
        "category": "service",
        "kind": "service",
        "schema": schema.model_dump(),
        "configured": {f: f in stored for f in schema.fields},
    }


# ── keyring + push (contract C4) ──


async def stored_pushable_credentials(name: str, schema: CredentialSchema) -> dict[str, str] | None:
    """Stored non-transient fields, or None unless every required one is present."""
    persistent = [f for f, spec in schema.fields.items() if not spec.transient]
    stored = await aget_all_secrets(name, persistent)
    required = [f for f, spec in schema.fields.items() if spec.required and not spec.transient]
    if any(f not in stored for f in required):
        return None
    return stored


async def push_credentials(http: httpx.AsyncClient, endpoint: str, fields: dict[str, str]) -> None:
    """POST credential fields as flat JSON to a service's credentials_endpoint.

    Raises httpx.HTTPError (connect failure or non-2xx) — callers decide policy.
    """
    response = await http.post(endpoint, json=fields)
    response.raise_for_status()


# ── health convention (GET status proxy) ──


def service_payload_healthy(status_code: int, payload: dict[str, Any]) -> bool:
    """Generic service-health convention — no service-specific keys in core.

    Healthy iff HTTP 200, top-level ``status == "ok"``, and every nested
    component dict that reports a ``state`` reports ``"connected"`` (e.g.
    home-service's ``ha.state``; see contract C6).
    """
    if status_code != 200 or payload.get("status") != "ok":
        return False
    return all(
        component.get("state") == "connected"
        for component in payload.values()
        if isinstance(component, dict) and "state" in component
    )
