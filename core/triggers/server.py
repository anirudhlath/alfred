"""FastAPI server for trigger engine — REST endpoints + JSON-RPC backward-compat shim."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import Body, FastAPI

if TYPE_CHECKING:
    from core.triggers.feature import TriggerFeature
    from sdk.alfred_sdk.client import AlfredClient

logger = logging.getLogger(__name__)


def create_app(client: AlfredClient, feature: TriggerFeature) -> FastAPI:
    """Build the FastAPI app with REST routes and JSON-RPC shim."""
    app = FastAPI(title="Trigger Engine", docs_url="/docs")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/triggers")
    async def create_trigger(body: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = await feature.create_trigger(**body)
        return result

    @app.get("/triggers")
    async def list_triggers(enabled_only: bool = True) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = await feature.list_triggers(enabled_only=enabled_only)
        return result

    @app.patch("/triggers/{trigger_id}")
    async def update_trigger(trigger_id: str, body: dict[str, Any]) -> dict[str, Any]:
        body.pop("trigger_id", None)  # prevent collision with path param
        result: dict[str, Any] = await feature.update_trigger(trigger_id=trigger_id, **body)
        return result

    @app.delete("/triggers/{trigger_id}")
    async def delete_trigger(trigger_id: str) -> dict[str, str]:
        result: dict[str, str] = await feature.delete_trigger(trigger_id=trigger_id)
        return result

    @app.patch("/triggers/{trigger_id}/toggle")
    async def toggle_trigger(trigger_id: str, enabled: bool = Body(embed=True)) -> dict[str, Any]:
        result: dict[str, Any] = await feature.toggle_trigger(
            trigger_id=trigger_id, enabled=enabled
        )
        return result

    # JSON-RPC backward-compat shim
    @app.post("/jsonrpc")
    async def jsonrpc_shim(body: dict[str, Any]) -> dict[str, Any]:
        method = body.get("method", "")
        params = body.get("params", {})
        req_id = body.get("id")
        try:
            result = await client.dispatch(method, params)
            return {"jsonrpc": "2.0", "result": result, "id": req_id}
        except Exception as e:
            logger.error("JSON-RPC error for method '%s': %s", method, e)
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32603, "message": str(e)},
                "id": req_id,
            }

    return app
