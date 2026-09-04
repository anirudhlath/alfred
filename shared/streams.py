"""Redis stream and key constants — single source of truth."""

EVENTS_STREAM = "alfred:events"
ACTIONS_STREAM = "alfred:actions"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"
# Consolidation feed. The ScratchpadWriter forwards here after appending to
# scratchpad.md, so the Librarian has its own queue to drain instead of racing
# the writer for SCRATCHPAD_QUEUE (which the writer always won).
LIBRARIAN_QUEUE = "alfred:librarian:queue"
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
# Passive observations are scored against their own frequency population.
# Sharing ENTITY_FREQUENCY_KEY would drive every count high enough that
# novelty (1/count) collapses to ~0 for real reflex actions too.
OBSERVED_FREQUENCY_KEY = "alfred:entity:freq:observed"

# Per-entry delivery-attempt counters for the Memory Ingestor (hash field ->
# count, whole-key TTL as a crash-safety net). Bounds retries of an entry that
# parses but fails deterministically downstream: without a cap it is reclaimed
# forever, burning a ZINCRBY on the observed-frequency key every pass and
# consuming the reclaim budget of everything behind it in the PEL.
INGEST_ATTEMPTS_KEY = "alfred:memory:ingest:attempts"

# Per-entity debounce for passive observation (SET NX EX). One noisy device
# accounted for 64% of qualifying events on the live instance, so this is
# what keeps episodic memory readable.
OBSERVED_ENTITY_PREFIX = "alfred:observer:seen:"

# Phase 3: Runtime config + cost
RUNTIME_CONFIG_KEY = "alfred:config:runtime"
COST_DAILY_KEY = "alfred:cost:daily"

# Phase 3: Integration registry
INTEGRATION_REGISTRY_KEY = "alfred:integration_registry"

# Attention set + pending critical actions (Real-Home HA Integration, Plan 3)
ATTENTION_PREFIX = "alfred:attention:"  # + domain → Redis SET of entity_ids
PENDING_ACTIONS_PREFIX = "alfred:pending_actions:"  # + request_id → ActionRequest JSON, TTL 300s

# Trigger cache coherence + user timezone
TRIGGERS_CHANGED_CHANNEL = "alfred:triggers:changed"
USER_TIMEZONE_KEY = "alfred:user:timezone"

# Ops carried on TRIGGERS_CHANGED_CHANNEL messages ({"op": ..., "trigger_id": ...})
TRIGGER_SYNC_OP_SAVED = "saved"
TRIGGER_SYNC_OP_DELETED = "deleted"
TRIGGER_SYNC_OP_TZ_CHANGED = "tz-changed"


# Auth (WebAuthn)
AUTH_SESSION_PREFIX: str = "alfred:auth:"
WEBAUTHN_CHALLENGE_PREFIX: str = "alfred:webauthn:challenge:"


def decode_stream_value(raw: str | bytes) -> str:
    """Decode a Redis stream value that may be bytes or already a string."""
    return raw.decode() if isinstance(raw, bytes) else raw
