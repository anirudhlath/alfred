"""Robinhood portfolio integration adapter.

Uses robin_stocks library for unofficial API access.
Requires Robinhood credentials stored in .env.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from core.integrations.base import (
    CredentialField,
    CredentialSchema,
    Integration,
    IntegrationCapability,
    IntegrationRequest,
    IntegrationResult,
)
from core.integrations.registry import IntegrationRegistry
from core.integrations.sanitizer import sanitize_response

logger = logging.getLogger(__name__)


@IntegrationRegistry.register()
class RobinhoodAdapter(Integration):
    """Fetches portfolio data from Robinhood."""

    name = "robinhood"
    category = "finance"

    credentials_schema = CredentialSchema(fields={
        "username": CredentialField(
            label="Email",
            placeholder="you@example.com",
        ),
        "password": CredentialField(
            label="Password",
            field_type="password",
        ),
        "mfa_code": CredentialField(
            label="MFA Code",
            required=False,
            transient=True,
            help_text="Optional — only needed for initial login, not stored",
        ),
    })

    def __init__(self, username: str = "", password: str = "", mfa_code: str = "") -> None:
        self._username = username
        self._password = password
        self._mfa_code = mfa_code
        self._logged_in = False

    async def get_capabilities(self) -> list[IntegrationCapability]:
        return [
            IntegrationCapability(
                name="get_portfolio",
                description="Get current portfolio summary",
                params_schema={"type": "object", "properties": {}},
            ),
            IntegrationCapability(
                name="get_positions",
                description="Get individual stock positions",
                params_schema={"type": "object", "properties": {}},
            ),
        ]

    def _ensure_login(self) -> bool:
        """Sync login (runs in executor)."""
        if self._logged_in:
            return True
        if not self._username:
            return False
        try:
            import robin_stocks.robinhood as rh

            rh.login(self._username, self._password, mfa_code=self._mfa_code)
            self._logged_in = True
            return True
        except Exception as e:
            logger.error("Robinhood login failed: %s", e)
            return False

    async def execute(self, request: IntegrationRequest) -> IntegrationResult:
        if not self._username:
            return IntegrationResult(
                data={"error": "Robinhood not configured"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

        import asyncio

        loop = asyncio.get_running_loop()
        logged_in = await loop.run_in_executor(None, self._ensure_login)
        if not logged_in:
            return IntegrationResult(
                data={"error": "Robinhood login failed"},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

        try:
            data = await loop.run_in_executor(None, self._fetch_data, request.action)
            clean = sanitize_response(data)
            return IntegrationResult(
                data=clean if isinstance(clean, dict) else {"data": clean},
                freshness=datetime.now(UTC),
                confidence=0.85,
            )
        except Exception as e:
            return IntegrationResult(
                data={"error": str(e)},
                freshness=datetime.now(UTC),
                confidence=0.0,
            )

    def _fetch_data(self, action: str) -> dict[str, Any]:
        import robin_stocks.robinhood as rh

        if action == "get_portfolio":
            profile = rh.profiles.load_portfolio_profile()
            return {
                "equity": profile.get("equity"),
                "extended_hours_equity": profile.get("extended_hours_equity"),
            }
        if action == "get_positions":
            positions = rh.account.build_holdings()
            return {"positions": positions}
        return {"error": f"Unknown action: {action}"}

    async def health_check(self) -> bool:
        if not self._username:
            return False
        import asyncio

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._ensure_login)
