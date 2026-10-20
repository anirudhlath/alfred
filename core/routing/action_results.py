"""Publishing an executed action's outcome for the clients waiting on it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from shared.streams import ACTION_RESULTS_MAXLEN, HOME_ACTION_RESULTS_STREAM

if TYPE_CHECKING:
    from bus.schemas.events import ActionResult
    from shared.types import AioRedis


async def publish_action_result(redis: AioRedis, result: ActionResult) -> None:
    """Append *result* to ``HOME_ACTION_RESULTS_STREAM``, which the PWA's Door waits on.

    The result's ``request_id`` is the pending record's id, so the Door can tie the
    outcome to the approval it showed.
    """
    await redis.xadd(
        HOME_ACTION_RESULTS_STREAM,
        {"event": result.model_dump_json()},
        maxlen=ACTION_RESULTS_MAXLEN,
        approximate=True,
    )
