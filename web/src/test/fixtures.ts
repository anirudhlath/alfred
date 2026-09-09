import type {
  AttentionDomain,
  IntegrationInfo,
  NotificationEvent,
  Overview,
  StreamPage,
} from "@/lib/types";

/**
 * `GET /api/integrations`. Two entries on purpose: the setup gate must find
 * `home-service` by name rather than by position.
 */
export const integrationsFixture: IntegrationInfo[] = [
  {
    name: "weather",
    category: "weather",
    kind: "adapter",
    schema: {
      fields: {
        api_key: {
          label: "API key",
          field_type: "password",
          required: true,
          placeholder: "",
          default: "",
          help_text: "",
          transient: false,
        },
      },
    },
    configured: { api_key: false },
  },
  {
    name: "home-service",
    category: "service",
    kind: "service",
    schema: {
      fields: {
        url: {
          label: "Home Assistant URL",
          field_type: "url",
          required: true,
          placeholder: "",
          default: "http://192.168.1.10:8123",
          help_text: "",
          transient: false,
        },
        token: {
          label: "Access Token",
          field_type: "password",
          required: true,
          placeholder: "",
          default: "",
          help_text: "Long-lived access token from your HA profile page",
          transient: false,
        },
      },
    },
    configured: { url: false, token: false },
  },
];

/**
 * `GET /api/admin/attention`. Three domains the reflex may be trusted with, and
 * the three it may never be: the setup gate must filter those out.
 */
export const attentionFixture: { domains: AttentionDomain[] } = {
  domains: [
    {
      domain: "light",
      members: ["light.hall", "light.kitchen", "light.living_room"],
      seen: [
        "light.hall",
        "light.kitchen",
        "light.living_room",
        "light.study",
        "light.landing",
        "light.porch",
      ],
    },
    {
      domain: "media_player",
      members: ["media_player.tv"],
      seen: ["media_player.tv", "media_player.kitchen"],
    },
    {
      domain: "fan",
      members: [],
      seen: ["fan.bathroom", "fan.study", "switch.desk", "switch.lamp"],
    },
    { domain: "lock", members: [], seen: ["lock.front_door", "lock.back_door"] },
    { domain: "alarm_control_panel", members: [], seen: ["alarm_control_panel.house"] },
    { domain: "cover", members: [], seen: ["cover.garage"] },
  ],
};

/** A house that has been running a while: 2.1 ev/s, $1.42 of the $5 cap, reflex at 380 ms. */
export const overviewFixture: Overview = {
  redis: { connected: true },
  cost: { date: "2026-09-07", spend_usd: 1.42, cap_usd: 5, request_count: 38, avg_usd: 0.037 },
  dnd: { active: false },
  counts: { sessions: 1, devices: 1, deferred: 0, triggers: 6 },
  streams: {
    events: { length: 412, last_id: "1788814440000-0", last_ts: 1788814440000, rate_5m: 1.4 },
    user_requests: { length: 18, last_id: "1788814380000-0", last_ts: 1788814380000, rate_5m: 0.2 },
    reflex_observations: {
      length: 96,
      last_id: "1788814420000-0",
      last_ts: 1788814420000,
      rate_5m: 0.5,
    },
  },
  inference: { ollama: true, lmstudio: false },
  reflex: { model: "reflex-3b", last_ms: 380, p50_ms: 372 },
  librarian: {
    last_run_at: "2026-09-07T03:00:00Z",
    reviewed: 42,
    next_run_at: "2026-09-08T03:00:00Z",
  },
};

/**
 * The same house on its first morning: every stream empty, nothing spent, nothing
 * measured, no triggers written, the Librarian yet to run for the first time.
 */
export const firstRunOverviewFixture: Overview = {
  ...overviewFixture,
  cost: { date: "2026-09-07", spend_usd: 0, cap_usd: 5, request_count: 0 },
  counts: { sessions: 1, devices: 1, deferred: 0, triggers: 0 },
  streams: {
    events: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
    user_requests: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
    reflex_observations: { length: 0, last_id: null, last_ts: null, rate_5m: 0 },
  },
  reflex: { model: "reflex-3b", last_ms: null, p50_ms: null },
  librarian: { last_run_at: null, reviewed: null, next_run_at: "2026-09-08T03:00:00Z" },
};

/**
 * Four stream pages as `GET /api/admin/streams/{name}?count=50` returns them:
 * newest first, each entry `{id, event}` with the event decoded from JSON.
 *
 * The clock in these is 2026-09-07: 17:58 a reflex act, 18:20 a notification,
 * 20:52 one conversational turn, and — an hour and a half later, so the
 * thirty-minute rule has something to fire on — 21:14 another.
 */
export const userRequestsPage: StreamPage = {
  entries: [
    {
      id: "1788815640000-0",
      event: {
        event_id: "ur-2",
        event_type: "user_request",
        timestamp: "2026-09-07T21:14:00",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f3",
        content_type: "text",
        content: "Is the back door locked?",
      },
    },
    {
      id: "1788811923000-0",
      event: {
        event_id: "ur-1",
        event_type: "user_request",
        timestamp: "2026-09-07T20:52:03",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f2",
        content_type: "text",
        content: "What have I got tomorrow morning?",
      },
    },
  ],
  next_before: null,
};

export const userResponsesPage: StreamPage = {
  entries: [
    {
      id: "1788815646000-0",
      event: {
        event_id: "al-2",
        event_type: "alfred_response",
        timestamp: "2026-09-07T21:14:06",
        source: "conscious-engine",
        session_id: "s_9f3",
        text: "It is, sir. Locked since 19:40.",
        actions_taken: ["home.get_state"],
        mood: "neutral",
      },
    },
    {
      id: "1788811926000-0",
      event: {
        event_id: "al-1",
        event_type: "alfred_response",
        timestamp: "2026-09-07T20:52:06",
        source: "conscious-engine",
        session_id: "s_9f2",
        text: "The dentist at nine, sir. I'd leave by twenty to; there's rain forecast from eight.",
        actions_taken: ["calendar.today", "weather.forecast"],
        mood: "pleased",
      },
    },
  ],
  next_before: null,
};

export const reflexObservationsPage: StreamPage = {
  entries: [
    {
      // Passive: seen, considered, nothing done. Never a Room row.
      id: "1788811000000-0",
      event: {
        observation_id: "obs-3",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T20:36:40",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "binary_sensor.motion_hall", new_state: "on" },
        action: null,
        result: null,
        decision_context: "user moving about, lights already on",
      },
    },
    {
      // Acted, and it failed. The meta says so.
      id: "1788807660000-0",
      event: {
        observation_id: "obs-2",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T19:41:00",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "fan.bathroom", new_state: "on" },
        action: { request_id: "8d2a", tool_name: "home.fan_set", target_service: "home-service" },
        result: { status: "error", error: "service unavailable" },
        decision_context: null,
      },
    },
    {
      id: "1788800280000-0",
      event: {
        observation_id: "obs-1",
        event_type: "reflex_observation",
        timestamp: "2026-09-07T17:58:00",
        source: "reflex-runner",
        origin: "state_change",
        trigger_event: { entity_id: "media_player.tv", new_state: "playing" },
        action: { request_id: "4b1d", tool_name: "home.light_set", target_service: "home-service" },
        result: { status: "success" },
        decision_context: "movie started, evening, user home",
      },
    },
  ],
  next_before: null,
};

export const notificationsPage: StreamPage = {
  entries: [
    {
      // A confirmation request: the Door's business, never a thread row.
      id: "1788801700000-0",
      event: {
        notification_id: "ntf-2",
        title: "Confirmation required",
        body: "Alfred wants to run 'home.lock_unlock' on home-service — confirm?",
        urgency: "urgent",
        source: "domain-router",
        timestamp: "2026-09-07T18:41:40",
        metadata: {
          pending_action_id: "a91f3c2e",
          tool_name: "home.lock_unlock",
          parameters: { entity_id: "lock.front_door", action: "unlock" },
          reason: null,
        },
      },
    },
    {
      id: "1788801600000-0",
      event: {
        notification_id: "ntf-1",
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "important",
        source: "trigger:trg_parcel",
        timestamp: "2026-09-07T18:20:00",
        metadata: {},
      },
    },
  ],
  next_before: null,
};

/** Yesterday, so the day-divider rule has two days to separate. */
export const yesterdayRequestPage: StreamPage = {
  entries: [
    {
      id: "1788721923000-0",
      event: {
        event_id: "ur-0",
        event_type: "user_request",
        timestamp: "2026-09-06T19:52:03",
        source: "web-pwa",
        channel: "web_pwa",
        session_id: "s_9f0",
        content_type: "text",
        content: "Lock up for the night.",
      },
    },
  ],
  next_before: null,
};

/** `GET /api/admin/notifications/deferred` — full Notification JSON, oldest first. */
export const deferredFixture: { notifications: NotificationEvent[] } = {
  notifications: [
    {
      notification_id: "ntf-d1",
      title: "Bins go out tonight",
      body: "The council moved collection to Friday.",
      urgency: "important",
      source: "trigger:trg_bins",
      timestamp: "2026-09-07T07:02:00",
      metadata: {},
    },
    {
      notification_id: "ntf-d2",
      title: "Bathroom humidity stayed high",
      body: "Above 70% for an hour.",
      urgency: "informational",
      source: "trigger:trg_bath",
      timestamp: "2026-09-07T07:19:00",
      metadata: {},
    },
  ],
};
