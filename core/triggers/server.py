"""HTTP server for trigger engine tool dispatch (JSON-RPC)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def handle_jsonrpc(
    request: dict[str, Any],
    client: Any,
) -> dict[str, Any]:
    """Handle a single JSON-RPC request by dispatching to the AlfredClient."""
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

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


async def run_server(
    client: Any,
    host: str = "0.0.0.0",
    port: int = 8001,
) -> None:
    """Run a minimal async HTTP server for JSON-RPC tool dispatch."""

    async def handle_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await reader.readline()  # consume request line (e.g. "POST / HTTP/1.1")
            headers: dict[str, str] = {}
            while True:
                header_line = await reader.readline()
                if header_line in (b"\r\n", b"\n", b""):
                    break
                key, _, value = header_line.decode().partition(":")
                headers[key.strip().lower()] = value.strip()

            content_length = int(headers.get("content-length", "0"))
            body = await reader.readexactly(content_length) if content_length else b""

            rpc_request: dict[str, Any] = json.loads(body) if body else {}
            rpc_response = await handle_jsonrpc(rpc_request, client)

            response_body = json.dumps(rpc_response).encode()
            http_response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: " + str(len(response_body)).encode() + b"\r\n"
                b"\r\n" + response_body
            )
            writer.write(http_response)
            await writer.drain()
        except Exception as e:
            logger.error("Server error: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_connection, host, port)
    logger.info("Trigger Engine HTTP server listening on %s:%d", host, port)

    async with server:
        await server.serve_forever()
