"""CostTracker — daily LLM spend tracking + budget enforcement."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, computed_field

from core.notifications.schema import Urgency
from shared.streams import COST_DAILY_KEY

if TYPE_CHECKING:
    from core.notifications.publisher import NotificationPublisher
    from shared.types import AioRedis

logger = logging.getLogger(__name__)


class CostState(BaseModel):
    """Daily LLM spend. Stored at alfred:cost:daily in Redis."""

    date: str  # ISO date YYYY-MM-DD
    spend_usd: float
    cap_usd: float
    alert_sent: bool = False
    request_count: int = 0  # handled user requests today; the Librarian's calls do not count

    @computed_field  # type: ignore[prop-decorator]
    @property
    def avg_usd(self) -> float:
        """Mean spend per recorded request today (0.0 before the first)."""
        return round(self.spend_usd / self.request_count, 6) if self.request_count else 0.0


# Approximate pricing per million tokens, keyed by shared.agents.model_key
# ("<provider>:<served name>"). A self-hosted model costs nothing per token.
_PRICING: dict[str, dict[str, float]] = {
    "openrouter:anthropic/claude-opus-4": {"input": 15.0, "output": 75.0},
    "openrouter:anthropic/claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "openrouter:anthropic/claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
    "openrouter:google/gemini-2.5-pro-preview": {"input": 1.25, "output": 10.0},
    "openrouter:deepseek/deepseek-chat-v3-0324": {"input": 0.27, "output": 1.1},
}
_FREE_PROVIDERS: frozenset[str] = frozenset({"vllm"})
_FREE = {"input": 0.0, "output": 0.0}
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


class CostTracker:
    """Tracks daily LLM spend against a configurable budget."""

    ALERT_THRESHOLD = 0.8  # Alert at 80% of cap

    def __init__(
        self,
        redis: AioRedis,
        daily_cap_usd: float = 5.0,
        notifier: NotificationPublisher | None = None,
    ) -> None:
        self._redis = redis
        self._daily_cap = daily_cap_usd
        self._notifier = notifier
        # Serialises record_spend's read-modify-write within this process (the
        # Librarian's concurrent compressions and System 2 share one tracker). Writers in
        # other processes can still interleave.
        self._spend_lock = asyncio.Lock()

    async def _get_state(self) -> CostState:
        """Get today's cost state from Redis, creating if needed."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        raw: bytes | str | None = await self._redis.get(COST_DAILY_KEY)

        if raw:
            state = CostState.model_validate_json(raw)
            if state.date == today:
                return state

        # New day or no state
        return CostState(date=today, spend_usd=0.0, cap_usd=self._daily_cap)

    async def _save_state(self, state: CostState) -> None:
        """Persist cost state to Redis with 48h TTL."""
        await self._redis.set(  # type: ignore[misc,unused-ignore]
            COST_DAILY_KEY, state.model_dump_json(), ex=172800
        )

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int, model: str) -> float:
        """Estimated USD cost of one model run's tokens."""
        provider = model.partition(":")[0]
        pricing = _FREE if provider in _FREE_PROVIDERS else _PRICING.get(model, _DEFAULT_PRICING)
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    async def record_spend(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        *,
        count_request: bool = True,
    ) -> CostState:
        """Record one model run's spend. *count_request* is False for background work
        (the Librarian), so ``request_count`` stays the number of handled requests."""
        cost = self._estimate_cost(prompt_tokens, completion_tokens, model)
        async with self._spend_lock:
            state = await self._get_state()
            state = state.model_copy(
                update={
                    "spend_usd": state.spend_usd + cost,
                    "cap_usd": self._daily_cap,
                    "request_count": state.request_count + (1 if count_request else 0),
                }
            )
            await self._save_state(state)
        logger.debug(
            "Recorded $%.4f spend (total: $%.2f / $%.2f)",
            cost,
            state.spend_usd,
            state.cap_usd,
        )
        return state

    async def is_budget_exceeded(self) -> bool:
        """Check if today's spend exceeds the daily cap."""
        state = await self._get_state()
        return state.spend_usd >= state.cap_usd

    async def should_send_alert(self) -> bool:
        """Check if spend has crossed the 80% alert threshold."""
        state = await self._get_state()
        return not state.alert_sent and state.spend_usd >= state.cap_usd * self.ALERT_THRESHOLD

    async def mark_alert_sent(self) -> None:
        """Mark that the 80% budget alert has been sent."""
        async with self._spend_lock:
            state = await self._get_state()
            state = state.model_copy(update={"alert_sent": True})
            await self._save_state(state)

    async def send_alert_if_needed(self) -> bool:
        """Check budget and send alert notification if threshold exceeded."""
        if not self._notifier:
            return False
        state = await self._get_state()
        if state.alert_sent or state.spend_usd < state.cap_usd * self.ALERT_THRESHOLD:
            return False
        await self._notifier.publish(
            title="Budget Warning",
            body=f"Daily spend ${state.spend_usd:.2f} has reached 80% of ${state.cap_usd:.2f} cap",
            source="cost_tracker",
            urgency=Urgency.URGENT,
        )
        # Re-read under the lock: spend recorded while the alert was publishing must stay.
        await self.mark_alert_sent()
        return True
