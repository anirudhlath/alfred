"""CostTracker — daily Claude API spend tracking + budget enforcement."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel

from shared.streams import COST_DAILY_KEY

if TYPE_CHECKING:
    from core.reflex.runner import AioRedis

logger = logging.getLogger(__name__)


class CostState(BaseModel):
    """Daily Claude API spend tracking. Stored at alfred:cost:daily in Redis."""

    date: str  # ISO date YYYY-MM-DD
    spend_usd: float
    cap_usd: float
    alert_sent: bool = False


# Approximate pricing per million tokens (via OpenRouter)
# Keyed by LiteLLM model string — update with actual pricing
_PRICING: dict[str, dict[str, float]] = {
    # Direct Anthropic
    "claude-opus-4-6": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    # OpenRouter prefixed
    "openrouter/anthropic/claude-opus-4": {"input": 15.0, "output": 75.0},
    "openrouter/anthropic/claude-sonnet-4": {"input": 3.0, "output": 15.0},
    "openrouter/anthropic/claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
    "openrouter/google/gemini-2.5-pro-preview": {"input": 1.25, "output": 10.0},
    "openrouter/deepseek/deepseek-chat-v3-0324": {"input": 0.27, "output": 1.1},
}
_DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


class CostTracker:
    """Tracks daily Claude API spend against a configurable budget."""

    ALERT_THRESHOLD = 0.8  # Alert at 80% of cap

    def __init__(self, redis: AioRedis, daily_cap_usd: float = 5.0) -> None:
        self._redis = redis
        self._daily_cap = daily_cap_usd

    async def _get_state(self) -> CostState:
        """Get today's cost state from Redis, creating if needed."""
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        raw: bytes | None = await self._redis.get(COST_DAILY_KEY)  # type: ignore[misc,unused-ignore]

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
        """Estimate cost in USD for a Claude API call."""
        pricing = _PRICING.get(model, _DEFAULT_PRICING)
        input_cost = (prompt_tokens / 1_000_000) * pricing["input"]
        output_cost = (completion_tokens / 1_000_000) * pricing["output"]
        return input_cost + output_cost

    async def record_spend(
        self, prompt_tokens: int, completion_tokens: int, model: str
    ) -> CostState:
        """Record spend for a Claude API call. Returns updated state."""
        state = await self._get_state()
        cost = self._estimate_cost(prompt_tokens, completion_tokens, model)
        state = CostState(
            date=state.date,
            spend_usd=state.spend_usd + cost,
            cap_usd=self._daily_cap,
            alert_sent=state.alert_sent,
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
        state = await self._get_state()
        state = CostState(
            date=state.date,
            spend_usd=state.spend_usd,
            cap_usd=state.cap_usd,
            alert_sent=True,
        )
        await self._save_state(state)
