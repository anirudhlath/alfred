"""Background startup warmup — eagerly initialize lazy-loaded components.

Services spawn a warmup task at startup so models (Whisper, Piper, embeddings,
Ollama) load while the service is already serving; the first real request then
skips the 10-60s lazy-load hit. Lazy-init paths stay in place as the safety
net, so a failed or unfinished warmup never breaks a request.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


async def _warm_one(service: str, name: str, step: Callable[[], Awaitable[object]]) -> None:
    t0 = time.monotonic()
    try:
        await step()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("warmup[{}]: {} failed ({}): {}", service, name, type(exc).__name__, exc)
        return
    logger.info("warmup[{}]: {} ready ({:.1f}s)", service, name, time.monotonic() - t0)


def start_warmup(
    service: str,
    steps: dict[str, Callable[[], Awaitable[object]]],
) -> asyncio.Task[None]:
    """Run all warmup steps concurrently in a background task.

    Failures are logged and non-fatal — the lazy-init path each step targets
    remains the fallback. Returns the task so callers can cancel it on shutdown.
    """

    async def _run() -> None:
        await asyncio.gather(*(_warm_one(service, name, step) for name, step in steps.items()))

    return asyncio.create_task(_run(), name=f"warmup-{service}")
