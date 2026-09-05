"""Entry point for the web channel server.

Usage: python -m core.channels
"""

from __future__ import annotations

import ipaddress
import os
import time

import uvicorn
from loguru import logger

from core.channels.voice_models import aget_tts
from core.channels.web_server import create_app, get_web_websockets
from core.notifications.adapters.websocket import WebSocketChannelAdapter
from core.notifications.channels import ChannelRegistry
from shared.config import AlfredConfig
from shared.logging import configure_logging

# uvicorn's own default: trust loopback only.
_DEFAULT_FORWARDED_ALLOW_IPS = "127.0.0.1"


def _resolve_forwarded_allow_ips() -> str:
    """Read FORWARDED_ALLOW_IPS, defaulting it, and warn about values that will not
    do what the operator expects. The result is handed to uvicorn verbatim.

    Behind a reverse proxy the peer address is the proxy; uvicorn rewrites
    request.client / scheme from X-Forwarded-For / -Proto, but only for peers listed
    here (IPs or CIDRs). Everything downstream — the trusted-network gate, Secure
    cookies, the 403 detail naming the client — depends on it.
    """
    # A blank value (how .env.example ships keys, and what compose injects for an
    # unset key) makes uvicorn trust *nothing*, so the headers are never rewritten
    # and the gate sees the proxy's own RFC1918 address — which would admit the
    # whole internet. Fall back to uvicorn's own loopback default instead.
    value = os.getenv("FORWARDED_ALLOW_IPS", "").strip() or _DEFAULT_FORWARDED_ALLOW_IPS

    # uvicorn only wildcards when the *whole* value is "*" (its always_trust flag).
    # Inside a list, "*" decays to a literal that matches no IP — so let it fall
    # through to the warning below rather than treating it as a valid entry.
    if value != "*":
        for raw_entry in value.split(","):
            candidate = raw_entry.strip()
            if not candidate:
                continue
            try:
                ipaddress.ip_network(candidate, strict=False)
            except ValueError:
                logger.warning(
                    "FORWARDED_ALLOW_IPS entry {!r} is not a valid IP or CIDR; uvicorn "
                    "keeps it as an exact literal, so it will never match an IP peer "
                    "and X-Forwarded-* stays ignored for that peer",
                    candidate,
                )

    # Heuristic — only the exact default is caught. Equivalents that are just as
    # loopback-only ("127.0.0.1/32", "127.0.0.1,::1", "localhost") will not warn.
    if value == _DEFAULT_FORWARDED_ALLOW_IPS:
        logger.warning(
            "FORWARDED_ALLOW_IPS is the loopback default — a reverse proxy on the "
            "container network will not match it, so X-Forwarded-* will not be "
            "rewritten; the trusted-network gate will see the proxy's own IP"
        )
    return value


def main() -> None:
    configure_logging(service="web-channel")
    config = AlfredConfig.from_env()

    # Wire channel adapters — only push to web/PWA clients.
    # iOS receives notifications via APNs; notification_id dedup is a safety net.
    # WebSocket adapter handles both text and TTS audio (URGENT only).
    ChannelRegistry.set_instance(
        "websocket",
        WebSocketChannelAdapter(get_sessions=get_web_websockets, aget_tts=aget_tts),
    )

    app = create_app(redis_url=config.redis_url)
    port = int(os.getenv("CHANNELS_PORT", "8081"))
    forwarded_allow_ips = _resolve_forwarded_allow_ips()
    logger.info("Trusting X-Forwarded-* headers from: {}", forwarded_allow_ips)
    for attempt in range(5):
        try:
            uvicorn.run(
                app,
                host="0.0.0.0",
                port=port,
                log_level="info",
                proxy_headers=True,
                forwarded_allow_ips=forwarded_allow_ips,
            )
            break
        except OSError as e:
            if e.errno == 48 and attempt < 4:
                wait = attempt + 1
                logger.warning("Port {} in use, retrying in {}s...", port, wait)
                time.sleep(wait)
            else:
                raise


if __name__ == "__main__":
    main()
