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

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import HTTPException
from loguru import logger
from pydantic import ValidationError

from bus.schemas.events import ServiceRegistered
from core.channels.stream_catalog import decode_entry
from core.integrations.base import CredentialSchema
from core.reflex.runner import ensure_consumer_group
from shared.redis_streams import read_group
from shared.secrets import aget_all_secrets
from shared.streams import EVENTS_STREAM, TOOL_REGISTRY_KEY, decode_stream_value

if TYPE_CHECKING:
    from shared.types import AioRedis

CREDENTIAL_PUSH_GROUP = "channels-credentials"


# ── registry reads ──


@dataclass(frozen=True)
class ServiceCredentialManifest:
    """A registry manifest paired with its already-validated credentials_schema.

    ``_parse_manifest`` validates ``credentials_schema`` exactly once per read;
    callers use ``.schema`` instead of re-parsing ``.manifest["credentials_schema"]``.
    """

    manifest: dict[str, Any]
    schema: CredentialSchema


def _parse_manifest(name: str, raw: bytes | str) -> ServiceCredentialManifest | None:
    """Decode + validate one registry manifest; None (logged) if malformed or unusable."""
    try:
        decoded: Any = json.loads(decode_stream_value(raw))
    except (TypeError, json.JSONDecodeError):
        logger.error("Invalid JSON in tool registry for service '{}'", name)
        return None
    if not isinstance(decoded, dict):
        logger.error("Non-object JSON in tool registry for service '{}'", name)
        return None
    manifest: dict[str, Any] = decoded
    schema_dict = manifest.get("credentials_schema")
    if not schema_dict:
        return None
    try:
        schema = CredentialSchema.model_validate(schema_dict)
    except ValidationError:
        logger.error("Malformed credentials_schema in registry for service '{}'", name)
        return None
    return ServiceCredentialManifest(manifest=manifest, schema=schema)


async def list_service_manifests(redis: AioRedis) -> dict[str, ServiceCredentialManifest]:
    """All registry manifests that declare a valid credentials_schema, keyed by service name."""
    raw: dict[bytes | str, bytes | str] = await redis.hgetall(TOOL_REGISTRY_KEY)
    manifests: dict[str, ServiceCredentialManifest] = {}
    for key, value in raw.items():
        name = key.decode() if isinstance(key, bytes) else key
        manifest = _parse_manifest(name, value)
        if manifest is not None:
            manifests[name] = manifest
    return manifests


async def get_service_manifest(redis: AioRedis, name: str) -> ServiceCredentialManifest | None:
    """One registry manifest; None if absent, malformed, or without a credentials_schema."""
    raw = await redis.hget(TOOL_REGISTRY_KEY, name)
    if raw is None:
        return None
    return _parse_manifest(name, raw)


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


# ── GET /api/integrations entry (contract C5) — shared by adapters + services ──


async def build_integration_entry(
    name: str, category: str, kind: str, schema: CredentialSchema
) -> dict[str, Any]:
    """Build one merged-integrations entry (adapter or registry-declared service)."""
    stored = await aget_all_secrets(name, list(schema.fields))
    return {
        "name": name,
        "category": category,
        "kind": kind,
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


# ── ServiceRegistered re-push worker ──


async def _handle_event_entry(
    redis: AioRedis,
    http: httpx.AsyncClient,
    entry_data: dict[bytes | str, bytes | str],
) -> None:
    """Process one alfred:events entry; push credentials for ServiceRegistered."""
    payload = decode_entry(entry_data)
    # alfred:events also carries TriggerFired/TriggerCreated — only act on ours.
    # decode_entry falls back to a dict without "event_type" for garbage/missing
    # payloads, so this check also covers the malformed-entry case (no crash).
    if payload.get("event_type") != "service_registered":
        return

    event = ServiceRegistered.model_validate(payload)
    if not event.has_credentials_schema or event.credentials_endpoint is None:
        return

    manifest = await get_service_manifest(redis, event.service_name)
    if manifest is None:
        logger.warning(
            "ServiceRegistered for '{}' but no registry manifest with a schema",
            event.service_name,
        )
        return

    fields = await stored_pushable_credentials(event.service_name, manifest.schema)
    if fields is None:
        logger.info("No stored credentials for '{}' — skipping push", event.service_name)
        return

    try:
        await push_credentials(http, event.credentials_endpoint, fields)
        logger.info(
            "Re-pushed credentials to '{}' at {}",
            event.service_name,
            event.credentials_endpoint,
        )
    except httpx.HTTPError as exc:
        # ACKed by the caller regardless — the retry vehicle is the service's
        # next ServiceRegistered (it re-registers on restart / re-connect).
        logger.warning("Credential push to '{}' failed: {}", event.service_name, exc)


async def credential_push_worker(
    redis: AioRedis,
    http: httpx.AsyncClient,
    consumer: str = "worker-1",
    shutdown: asyncio.Event | None = None,
) -> None:
    """Consume ServiceRegistered from alfred:events and re-push stored credentials.

    Runs in the channels process with its own consumer group (contract C5:
    ``channels-credentials``). Same worker pattern as
    core/notifications/delivery.py::notification_delivery_worker.
    """
    await ensure_consumer_group(redis, EVENTS_STREAM, CREDENTIAL_PUSH_GROUP)
    _shutdown = shutdown or asyncio.Event()

    while not _shutdown.is_set():
        try:
            entries = await read_group(
                redis, CREDENTIAL_PUSH_GROUP, consumer, {EVENTS_STREAM: ">"}, count=10, block=5000
            )

            for _stream_key, stream_entries in entries:
                for entry_id, entry_data in stream_entries:
                    await _handle_event_entry(redis, http, entry_data)
                    await redis.xack(EVENTS_STREAM, CREDENTIAL_PUSH_GROUP, entry_id)

        except Exception as e:
            if not _shutdown.is_set():
                logger.error("Credential push worker error: {}", e)
                await asyncio.sleep(1)
