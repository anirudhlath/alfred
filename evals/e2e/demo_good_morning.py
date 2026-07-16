"""End-to-end Good Morning demo — exercises every Phase 3 component.

Usage: python -m evals.e2e.demo_good_morning [--channel web_pwa|signal|voice]

This script:
1. Publishes a UserRequest ("Good morning") to alfred:user:requests
2. Waits for an AlfredResponse on alfred:user:responses
3. Prints the response with timing
4. Verifies the response passes eval metrics
5. (Optional) Checks SigNoz for the complete trace

Requires all Phase 3 services running:
- Redis + Mosquitto (infrastructure)
- home-service (tool registration)
- Reflex Engine (System 1)
- Conscious Engine (System 2)
- Web channel or Signal bridge (for real channel test)
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

import redis.asyncio as aioredis

from bus.schemas.events import AlfredResponse, UserRequest
from evals.conscious.metrics import ButlerPersonalityScore, PrivacyLeakScore
from shared.config import AlfredConfig
from shared.logging import configure_logging
from shared.streams import USER_REQUESTS_STREAM, USER_RESPONSES_STREAM, decode_stream_value

if TYPE_CHECKING:
    from loguru import Logger


ChannelType = Literal["web_pwa", "signal", "voice"]


async def run_demo(channel: str = "web_pwa") -> None:
    """Run the Good Morning demo end-to-end."""
    log: Logger = configure_logging(service="demo")
    config = AlfredConfig.from_env()
    r = aioredis.from_url(config.redis_url)

    channel_typed = cast("ChannelType", channel)
    session_id = str(uuid4())
    request = UserRequest(
        source="demo-script",
        channel=channel_typed,
        session_id=session_id,
        identity_claim="sir",
        content_type="text",
        content="Good morning",
    )

    log.info("=" * 60)
    log.info("GOOD MORNING DEMO")
    log.info("=" * 60)
    log.info("Channel: {} | Session: {}", channel, session_id)

    # Publish request
    start = time.monotonic()
    await r.xadd(USER_REQUESTS_STREAM, {"event": request.model_dump_json()})
    log.info("Published UserRequest at t=0ms")

    # Wait for response
    last_id = "0-0"
    timeout = 30.0
    response: AlfredResponse | None = None

    while (time.monotonic() - start) < timeout:
        entries: list[
            tuple[bytes | str, list[tuple[bytes | str, dict[bytes | str, bytes | str]]]]
        ] = await r.xread(  # type: ignore[assignment,misc,unused-ignore]
            {USER_RESPONSES_STREAM: last_id}, count=10, block=1000
        )
        for _stream, stream_entries in entries:
            for entry_id, entry_data in stream_entries:
                last_id = decode_stream_value(entry_id)
                raw = entry_data.get(b"event") or entry_data.get("event")
                if raw:
                    event_str = raw.decode() if isinstance(raw, bytes) else raw
                    resp = AlfredResponse.model_validate_json(event_str)
                    if resp.session_id == session_id:
                        response = resp
                        break
            if response:
                break
        if response:
            break

    elapsed = (time.monotonic() - start) * 1000

    if response is None:
        log.error("No response received within {:.0f}s!", timeout)
        await r.aclose()
        return

    log.info("-" * 60)
    log.info("RESPONSE ({:.0f}ms):", elapsed)
    log.info("-" * 60)
    log.info("")
    for line in response.text.split("\n"):
        log.info("  {}", line)
    log.info("")
    log.info("Actions taken: {}", response.actions_taken)
    log.info("Mood: {}", response.mood)

    # Eval metrics
    log.info("-" * 60)
    log.info("EVAL METRICS:")
    log.info("-" * 60)

    butler = ButlerPersonalityScore()
    butler_score = butler.score(response.text)
    butler_status = "PASS" if butler_score >= 0.6 else "FAIL"
    log.info("  Butler Personality: {:.2f} {}", butler_score, butler_status)

    privacy = PrivacyLeakScore()
    privacy_score = privacy.score(response.text, "sir")
    privacy_status = "PASS" if privacy_score >= 0.9 else "FAIL"
    log.info("  Privacy (sir):     {:.2f} {}", privacy_score, privacy_status)

    # Check for expected topics
    topics = ["sleep", "meeting", "weather", "portfolio"]
    resp_lower = response.text.lower()
    for topic in topics:
        present = topic in resp_lower
        log.info("  Mentions {:<10s} {}", topic + ":", "PASS" if present else "FAIL")

    log.info("-" * 60)
    log.info("Latency: {:.0f}ms (target: <3000ms)", elapsed)
    log.info("=" * 60)

    await r.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Alfred Good Morning Demo")
    parser.add_argument("--channel", default="web_pwa", choices=["web_pwa", "signal", "voice"])
    args = parser.parse_args()
    asyncio.run(run_demo(channel=args.channel))


if __name__ == "__main__":
    main()
