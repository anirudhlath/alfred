"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .mcp import mcp_tool
from .telemetry import track_event, track_latency, track_tokens

__all__ = [
    "AlfredClient",
    "mcp_tool",
    "track_event",
    "track_latency",
    "track_tokens",
]
