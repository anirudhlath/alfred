// Sample data for the preview stories. Shapes are the web client's own types
// (type-checked against web/src by `tsc -p tsconfig.previews.json`), values are
// plausible house data. The components decide what is drawn; nothing here is
// chosen to make a screen match a mock-up.
import type { TrackedAction } from "@/lib/actions";
import type { FeedRow } from "@/lib/feed";
import { tombstoneItems } from "@/lib/actions";
import { SESSION_IDLE_MS, toTimelineItems, withDividers, type RoomHistory, type TimelineItem } from "@/lib/history";
import type { Routine } from "@/lib/memory";
import type { StreamName } from "@/lib/streams";
import type { Trigger } from "@/lib/triggers";
import type { Overview } from "@/lib/types";

/** One fixed instant, so fuses and "tomorrow" labels do not move between renders. */
export const NOW = Date.parse("2026-10-03T07:42:48Z");
const iso = (offsetS: number): string => new Date(NOW + offsetS * 1000).toISOString();

export const overview: Overview = {
  redis: { connected: true },
  cost: { date: "2026-10-03", spend_usd: 1.42, cap_usd: 5, request_count: 38, avg_usd: 0.037 },
  dnd: { active: false },
  counts: { sessions: 2, devices: 1, deferred: 2, triggers: 6 },
  streams: {
    user_requests: { length: 412, last_id: "1759477368000-0", last_ts: NOW, rate_5m: 0.2 },
    events: { length: 9120, last_id: "1759477368000-1", last_ts: NOW, rate_5m: 1.9 },
  },
  inference: { ollama: true, lmstudio: false },
  reflex: { model: "reflex-3b", last_ms: 380, p50_ms: 410 },
  librarian: { last_run_at: iso(-17_000), reviewed: 42, next_run_at: iso(69_000) },
  session: { idle_minutes: 30 },
};

export const firstRunOverview: Overview = {
  ...overview,
  cost: { date: "2026-10-03", spend_usd: 0, cap_usd: 5 },
  streams: {},
  reflex: { model: "reflex-3b", last_ms: null, p50_ms: null },
};

export const lastTrue = new Date(NOW - 6 * 60_000);

export const pendingAction: TrackedAction = {
  phase: "pending",
  action: {
    request_id: "a91f3c2e-55d0-4b8e-9d7a-0c1e2f3a4b5c",
    tool_name: "home.lock_unlock",
    target_service: "home",
    parameters: { entity_id: "lock.front_door", action: "unlock" },
    reason: "You asked me to let the cleaner in when she rings. She rang at 07:41.",
    source: "conscious-engine",
    timestamp: iso(-48),
    ttl_seconds: 300,
    expires_at: iso(252),
  },
};

export const dangerAction: TrackedAction = {
  ...pendingAction,
  action: { ...pendingAction.action, expires_at: iso(21) },
};

export const queuedAction: TrackedAction = { ...pendingAction, phase: "queued", confirmedAt: iso(-3) };
export const appliedAction: TrackedAction = {
  ...pendingAction,
  phase: "applied",
  confirmedAt: iso(-9),
  appliedAt: iso(-6),
};
export const expiredAction: TrackedAction = {
  ...pendingAction,
  phase: "expired",
  action: { ...pendingAction.action, expires_at: iso(-60) },
};

/** Raw Room streams, as `/api/admin/streams/*` returns them. */
export const history: RoomHistory = {
  user_requests: [
    { id: `${NOW - 3_500_000}-0`, event: { timestamp: iso(-3500), content: "What's on this morning?", channel: "web_pwa" } },
    { id: `${NOW - 20_000}-0`, event: { timestamp: iso(-20), content: "Is the back door locked?", channel: "web_pwa" } },
  ],
  user_responses: [
    {
      id: `${NOW - 3_490_000}-0`,
      event: {
        timestamp: iso(-3490),
        text: "Two things, sir: the dentist at 10:30 and a call with Priya at 14:00. Light rain until noon.",
        mood: "neutral",
        actions_taken: ["calendar.today", "weather.forecast"],
      },
    },
  ],
  reflex_observations: [
    {
      id: `${NOW - 1_200_000}-0`,
      event: {
        timestamp: iso(-1200),
        decision_context: "Hall lights down to 30% for the morning",
        action: { tool_name: "home.light_turn_on", request_id: "4b1d9e70" },
        result: { status: "ok" },
      },
    },
  ],
  notifications: [
    {
      id: `${NOW - 600_000}-0`,
      event: { timestamp: iso(-600), title: "Bins night", body: "Put the bins out, sir.", source: "trigger", urgency: "important" },
    },
  ],
};

/** The Room's thread, made the way the client makes it: rows, tombstones, then dividers. */
export const items: TimelineItem[] = [
  ...withDividers(
    [...toTimelineItems(history), ...tombstoneItems([expiredActionEarlier()])].sort(
      (a, b) => Date.parse(a.at) - Date.parse(b.at),
    ),
    new Date(NOW),
    SESSION_IDLE_MS,
  ),
  { kind: "thinking", id: "thinking", at: iso(-10), detail: "home.get_state running" },
];

function expiredActionEarlier(): TrackedAction {
  return {
    ...pendingAction,
    phase: "expired",
    action: { ...pendingAction.action, timestamp: iso(-2100), expires_at: iso(-1800) },
  };
}

function entry(stream: StreamName, offsetMs: number, event: Record<string, unknown>): FeedRow {
  const id = `${NOW + offsetMs}-0`;
  return { stream, key: `${stream}:${id}`, entry: { id, event } };
}

export const feed: FeedRow[] = [
  entry("reflex_observations", -1000, {
    source: "reflex",
    trigger_event: { entity_id: "media_player.living_room_tv" },
    decision_context: "movie started, evening",
    latency_ms: 380,
  }),
  entry("home_state", -4000, {
    entity_id: "binary_sensor.front_door",
    old_state: "off",
    new_state: "on",
  }),
  entry("actions", -9000, {
    tool_name: "home.light_turn_on",
    target_service: "home",
    parameters: { entity_id: "light.hall", brightness_pct: 30 },
    request_id: "4b1d9e70-1c2a-4f3e-8b6d-2e9f0a1b2c3d",
    source: "reflex",
  }),
  entry("events", -16000, {
    event_type: "trigger_fired",
    trigger_name: "bins night",
    fired_by: "engine",
    trigger_type: "time",
    urgency: "important",
  }),
  entry("user_requests", -30000, {
    content: "Is the back door locked?",
    session_id: "s-7c21",
    channel: "web_pwa",
    content_type: "text",
  }),
];

export const counts: Record<StreamName, number> = {
  user_requests: 12,
  user_responses: 12,
  events: 48,
  actions: 9,
  reflex_observations: 31,
  notifications: 4,
  home_state: 220,
  home_action_results: 9,
};

export const routines: Routine[] = [
  {
    name: "Evening wind-down",
    trigger_pattern: "media_player.living_room_tv:playing after 21:00",
    steps: [
      {
        description: "Dim the living room to 20%",
        action: {
          tool_name: "home.light_turn_on",
          target_service: "home",
          parameters: { entity_id: "light.living_room", brightness_pct: 20 },
        },
      },
    ],
    confidence: 0.86,
    learned_from: ["ep-1", "ep-2", "ep-3"],
    state: "candidate",
    last_hit: iso(-86_400),
    consecutive_misses: 0,
    last_suggested: iso(-43_200),
    confidence_history: [0.52, 0.58, 0.63, 0.7, 0.74, 0.79, 0.82, 0.86],
  },
  {
    name: "Weekend coffee",
    trigger_pattern: "kitchen motion 08:00–09:00 sat,sun",
    steps: [{ description: "Turn on the kettle plug", action: null }],
    confidence: 0.41,
    learned_from: ["ep-9"],
    state: "dormant",
    last_hit: iso(-14 * 86_400),
    consecutive_misses: 3,
    last_suggested: iso(-7 * 86_400),
    confidence_history: [0.6, 0.58, 0.55, 0.5, 0.47, 0.45, 0.43, 0.41],
  },
];

export const triggers: Trigger[] = [
  {
    trigger_id: "trg-bins",
    trigger_type: "time",
    name: "Bins night",
    enabled: true,
    one_shot: false,
    created_by: "conscious-engine",
    created_at: iso(-30 * 86_400),
    last_fired: iso(-600),
    action: null,
    urgency: "important",
    conditions: { cron: "30 19 * * 3" },
  },
  {
    trigger_id: "trg-dentist",
    trigger_type: "time",
    name: "Leave for the dentist",
    enabled: true,
    one_shot: true,
    created_by: "conscious-engine",
    created_at: iso(-3400),
    last_fired: null,
    action: null,
    urgency: "important",
    conditions: { run_at: iso(8_400) },
  },
  {
    trigger_id: "trg-door",
    trigger_type: "sensor",
    name: "Front door left open",
    enabled: false,
    one_shot: false,
    created_by: "conscious-engine",
    created_at: iso(-12 * 86_400),
    last_fired: iso(-2 * 86_400),
    action: null,
    urgency: "urgent",
    conditions: { entity_id: "binary_sensor.front_door", state: "on", for_seconds: 120 },
  },
];

/** One row of each kind, picked from the thread the client built above. */
export function itemOf<K extends TimelineItem["kind"]>(kind: K, nth = 0): Extract<TimelineItem, { kind: K }> {
  const found = items.filter((i): i is Extract<TimelineItem, { kind: K }> => i.kind === kind)[nth];
  if (!found) throw new Error(`no ${kind} item #${nth} in the fixture thread`);
  return found;
}

/** What `tombstoneItems` makes of an approval that lapsed and one answered elsewhere. */
export const tombstones = tombstoneItems([
  expiredAction,
  { ...pendingAction, phase: "answered" },
]) as Extract<TimelineItem, { kind: "tombstone" }>[];
