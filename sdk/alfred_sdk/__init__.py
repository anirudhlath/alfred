"""alfred-sdk — the only coupling between Alfred and external applications."""

from .client import AlfredClient
from .feature import BaseFeature, CredentialField, CredentialSchema, tool
from .telemetry import track_event, track_latency, track_tokens

__all__ = [
    "AlfredClient",
    "BaseFeature",
    "CredentialField",
    "CredentialSchema",
    "tool",
    "track_event",
    "track_latency",
    "track_tokens",
]
