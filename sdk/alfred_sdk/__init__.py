"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .feature import BaseFeature, CredentialField, CredentialSchema, ToolArgumentError, tool
from .live_state import LiveStateWriter, read_live_state
from .telemetry import track_event, track_latency, track_tokens

__all__ = [
    "AlfredClient",
    "BaseFeature",
    "CredentialField",
    "CredentialSchema",
    "LiveStateWriter",
    "ToolArgumentError",
    "read_live_state",
    "tool",
    "track_event",
    "track_latency",
    "track_tokens",
]
