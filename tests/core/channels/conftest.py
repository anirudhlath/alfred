"""Shared fixtures for web channel tests."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from core.channels.web_server import create_app


@pytest.fixture
def web_client() -> TestClient:
    """Create a TestClient with a mocked Redis connection."""
    app = create_app(redis_url="redis://localhost:6379")
    app.state.redis = AsyncMock()
    return TestClient(app)
