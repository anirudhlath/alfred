"""The harness's own failure. A leaf module, so the collectors the driver calls can raise it
without importing the driver."""

from __future__ import annotations


class HarnessError(RuntimeError):
    """The harness, not Alfred, failed. The sample scores E and is retried once."""
