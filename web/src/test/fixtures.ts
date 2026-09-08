import type { AttentionDomain, IntegrationInfo, Overview } from "@/lib/types";

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
