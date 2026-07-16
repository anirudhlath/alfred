"""Redis stream and key constants — single source of truth."""

EVENTS_STREAM = "alfred:events"
ACTIONS_STREAM = "alfred:actions"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"
TRIGGERS_KEY = "alfred:triggers"
TOOL_REGISTRY_KEY = "alfred:tool_registry"
CONTEXT_KEY_PREFIX = "alfred:context:"

# Home domain streams (used by MQTT bridge + Reflex Runner)
HOME_STATE_STREAM = "alfred:home:state_changed"
HOME_ACTION_RESULTS_STREAM = "alfred:home:action_results"
REFLEX_OBSERVATIONS_STREAM = "alfred:reflex:observations"

# Phase 3: Conscious Engine
USER_REQUESTS_STREAM = "alfred:user:requests"
USER_RESPONSES_STREAM = "alfred:user:responses"
SESSIONS_KEY_PREFIX = "alfred:sessions:"
DND_STATE_KEY = "alfred:memory:dnd"
DEFERRED_NOTIFICATIONS_KEY = "alfred:notifications:deferred"
NOTIFICATION_DISPATCH_STREAM = "alfred:notifications:dispatch"
DEVICE_TOKENS_KEY = "alfred:push:devices"

# Phase 3: Memory
VOICEPRINT_KEY = "alfred:identity:voiceprint"

# Unified context index (RediSearch)
CONTEXT_INDEX = "idx:context"
CONTEXT_PREFIX = "ctx:"
ENTITY_FREQUENCY_KEY = "alfred:entity:freq"

# Phase 3: Runtime config + cost
RUNTIME_CONFIG_KEY = "alfred:config:runtime"
COST_DAILY_KEY = "alfred:cost:daily"

# Phase 3: Integration registry
INTEGRATION_REGISTRY_KEY = "alfred:integration_registry"

# Trigger cache coherence + user timezone
TRIGGERS_CHANGED_CHANNEL = "alfred:triggers:changed"
USER_TIMEZONE_KEY = "alfred:user:timezone"


# Auth (WebAuthn)
AUTH_SESSION_PREFIX: str = "alfred:auth:"
WEBAUTHN_CHALLENGE_PREFIX: str = "alfred:webauthn:challenge:"


def decode_stream_value(raw: str | bytes) -> str:
    """Decode a Redis stream value that may be bytes or already a string."""
    return raw.decode() if isinstance(raw, bytes) else raw
