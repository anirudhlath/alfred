"""Redis stream and key constants — single source of truth."""

EVENTS_STREAM = "alfred:events"
ACTIONS_STREAM = "alfred:actions"
SCRATCHPAD_QUEUE = "alfred:scratchpad:queue"
TRIGGERS_KEY = "alfred:triggers"
TOOL_REGISTRY_KEY = "alfred:tool_registry"
CONTEXT_KEY_PREFIX = "alfred:context:"

# Phase 3: Conscious Engine
USER_REQUESTS_STREAM = "alfred:user:requests"
USER_RESPONSES_STREAM = "alfred:user:responses"
SESSIONS_KEY_PREFIX = "alfred:sessions:"
NOTIFICATIONS_STREAM = "alfred:notifications:queue"

# Phase 3: Memory
EPISODIC_STREAM = "alfred:memory:episodic"
VOICEPRINT_KEY = "alfred:identity:voiceprint"

# Phase 3: Runtime config + cost
RUNTIME_CONFIG_KEY = "alfred:config:runtime"
COST_DAILY_KEY = "alfred:cost:daily"

# Phase 3: Integration registry
INTEGRATION_REGISTRY_KEY = "alfred:integration_registry"
