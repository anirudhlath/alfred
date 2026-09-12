import { afterEach, describe, expect, it, vi } from "vitest";
import type { StreamPage } from "./types";
import type { StreamName, StreamRef } from "./streams";
import {
  buildThread,
  fetchThread,
  fetchThreadCandidates,
  JOIN_WINDOW_MS,
  MAX_ADJACENT,
  nodeMeta,
  type ThreadNode,
} from "./trace";

// 21:13:20 on the device's clock, so hhmmss reads the same under CI's UTC and on the phone.
const T0 = new Date(2026, 8, 7, 21, 13, 20).getTime();

function ref(stream: StreamName, ms: number, event: Record<string, unknown>): StreamRef {
  return { stream, entry: { id: `${ms}-0`, event } };
}

// The movie thread: the TV starts, the reflex dims the lights, the light reports, the result lands, the observation is written.
const TV = ref("home_state", T0, {
  event_id: "9c41e0aa",
  entity_id: "media_player.tv",
  old_state: "idle",
  new_state: "playing",
  domain: "media",
  source: "home-service",
});
const ACTION = ref("actions", T0 + 500, {
  event_id: "1f22b7c0",
  request_id: "4b1d9e00",
  tool_name: "home.light_set",
  parameters: { entity_id: "light.living_room", brightness: 30 },
  target_service: "home-service",
  source: "reflex-engine",
});
const LIGHT = ref("home_state", T0 + 1200, {
  event_id: "7d80aa31",
  entity_id: "light.living_room",
  old_state: "off",
  new_state: "on",
  domain: "home",
  source: "home-service",
});
const RESULT = ref("home_action_results", T0 + 1500, {
  event_id: "c3d4e5f6",
  request_id: "4b1d9e00",
  tool_name: "home.light_set",
  status: "success",
});
const ANCHOR = ref("reflex_observations", T0 + 2000, {
  event_id: "e8f9a0b1",
  origin: "state_change",
  trigger_event: TV.entry.event,
  action: ACTION.entry.event,
  result: { status: "success" },
  decision_context: "movie started, evening, user home",
});
const PARCEL = ref("notifications", T0 + 3000, {
  notification_id: "n-1",
  title: "Parcel",
  body: "Your parcel arrived",
  urgency: "informational",
  source: "trigger:trg_parcel",
});

function ids(nodes: { entry: { id: string } }[]): string[] {
  return nodes.map((node) => node.entry.id);
}

function linkOf(nodes: ThreadNode[], target: StreamRef): ThreadNode["link"] | undefined {
  return nodes.find((node) => node.stream === target.stream && node.entry.id === target.entry.id)?.link;
}

function node(nodes: ThreadNode[], target: StreamRef): ThreadNode {
  const found = nodes.find((n) => n.stream === target.stream && n.entry.id === target.entry.id);
  if (!found) throw new Error(`${target.stream}:${target.entry.id} is not in the thread`);
  return found;
}

describe("buildThread", () => {
  it("is the anchor alone when nothing joins it or is near it", () => {
    const far = ref("user_requests", T0 + 400_000, { event_id: "u1", session_id: "s_1", content: "hi" });
    expect(buildThread(ANCHOR, [far])).toEqual([{ ...ANCHOR, link: "anchor" }]);
  });

  it("joins the state change the observation was triggered by, by event_id", () => {
    expect(linkOf(buildThread(ANCHOR, [TV]), TV)).toEqual({ key: "event_id", value: "9c41e0aa" });
  });

  it("joins the action, its result and its confirmation by request_id", () => {
    const confirm = ref("notifications", T0 + 700, {
      notification_id: "n-2",
      title: "Confirm?",
      body: "Dim the lights?",
      urgency: "actionable",
      source: "domain-router",
      metadata: { pending_action_id: "4b1d9e00" },
    });
    const nodes = buildThread(ANCHOR, [ACTION, RESULT, confirm]);
    const byRequest = { key: "request_id", value: "4b1d9e00" };
    expect(linkOf(nodes, ACTION)).toEqual(byRequest);
    expect(linkOf(nodes, RESULT)).toEqual(byRequest);
    expect(linkOf(nodes, confirm)).toEqual(byRequest);
  });

  it("joins the state change of the entity the observation's action set", () => {
    // LIGHT shares no id with the anchor; it is the entity the observation's action named.
    expect(linkOf(buildThread(ANCHOR, [LIGHT]), LIGHT)).toEqual({
      key: "entity_id",
      value: "light.living_room",
    });
  });

  it("follows joins through nodes it has admitted: the reply naming the tool, then the request in its session", () => {
    const reply = ref("user_responses", T0 + 2500, {
      event_id: "r1",
      session_id: "s_9f3",
      text: "Dimmed.",
      actions_taken: ["home.light_set"],
    });
    const request = ref("user_requests", T0 - 8000, { event_id: "q1", session_id: "s_9f3", content: "Movie time" });

    // Only an `actions` entry can be named by a reply — without ACTION, neither joins.
    const without = buildThread(ANCHOR, [reply, request]);
    expect(linkOf(without, reply)).toBe("adjacent");
    expect(linkOf(without, request)).toBeUndefined();

    const nodes = buildThread(ANCHOR, [ACTION, reply, request]);
    expect(linkOf(nodes, reply)).toEqual({ key: "actions_taken", value: "home.light_set" });
    expect(linkOf(nodes, request)).toEqual({ key: "session_id", value: "s_9f3" });
  });

  it("does not credit a reply written before the action it would have named", () => {
    const earlier = ref("user_responses", T0 - 1000, {
      event_id: "r0",
      session_id: "s_9f3",
      text: "Earlier.",
      actions_taken: ["home.light_set"],
    });
    expect(linkOf(buildThread(ANCHOR, [ACTION, earlier]), earlier)).toBe("adjacent");
  });

  it("attributes a state change to an action only within a minute of it", () => {
    // 61 s after the observation, longer after the action it carries.
    const late = ref("home_state", T0 + 2000 + 61_000, LIGHT.entry.event);
    expect(linkOf(buildThread(ANCHOR, [ACTION, late]), late)).toBeUndefined();
  });

  it("ignores an id join outside the ten-minute window", () => {
    const stale = ref("home_action_results", T0 - JOIN_WINDOW_MS - 1, RESULT.entry.event);
    expect(ids(buildThread(ANCHOR, [stale]))).toEqual([ANCHOR.entry.id]);
  });

  it("shows what is merely near in time as adjacent, nearest the anchor first, at most six", () => {
    const near = Array.from({ length: 8 }, (_, i) =>
      ref("home_state", T0 + 2000 + (i + 1) * 100, { event_id: `n${i}`, entity_id: `sensor.${i}`, new_state: String(i) }),
    );
    const nodes = buildThread(ANCHOR, [...near, PARCEL]);
    const adjacent = nodes.filter((n) => n.link === "adjacent");
    expect(adjacent).toHaveLength(MAX_ADJACENT);
    expect(ids(adjacent)).toEqual(ids(near.slice(0, MAX_ADJACENT)));
    expect(linkOf(nodes, PARCEL)).toBeUndefined();
  });

  it("is adjacent to any joined node, not only the anchor", () => {
    // 4 s before TV (joined), 6 s before the anchor.
    const nearTv = ref("events", T0 - 4000, {
      event_id: "ev-1",
      event_type: "trigger_fired",
      trigger_id: "trg_x",
      trigger_name: "x",
    });
    expect(linkOf(buildThread(ANCHOR, [nearTv]), nearTv)).toBeUndefined();
    expect(linkOf(buildThread(ANCHOR, [TV, nearTv]), nearTv)).toBe("adjacent");
  });

  it("orders oldest first, catalogue order on a tie", () => {
    // The same event as TV, republished on `events` at the same millisecond.
    const twin = ref("events", T0, { event_id: "9c41e0aa", event_type: "state_changed" });
    const nodes = buildThread(ANCHOR, [RESULT, TV, ACTION, LIGHT, twin]);
    expect(nodes.map((n) => `${n.stream}@${n.entry.id}`)).toEqual([
      `events@${T0}-0`,
      `home_state@${T0}-0`,
      `actions@${T0 + 500}-0`,
      `home_state@${T0 + 1200}-0`,
      `home_action_results@${T0 + 1500}-0`,
      `reflex_observations@${T0 + 2000}-0`,
    ]);
  });
});

describe("nodeMeta", () => {
  it("stamps, names the stream, carries the summary meta and says how the node got here", () => {
    const nodes = buildThread(ANCHOR, [ACTION, PARCEL]);
    expect(nodeMeta(node(nodes, ANCHOR))).toBe(
      '21:13:22 · RX · home.light_set · request 4b1d · decision "movie started, evening, user home" · this row',
    );
    expect(nodeMeta(node(nodes, ACTION))).toBe(
      "21:13:20 · AC · request 4b1d · home-service · reflex-engine · joined by request_id 4b1d",
    );
    expect(nodeMeta(node(nodes, PARCEL))).toBe(
      "21:13:23 · NT · informational · trigger:trg_parcel · adjacent in time only",
    );
  });

  it("prints a named join in full and an id join short", () => {
    const nodes = buildThread(ANCHOR, [ACTION, LIGHT, TV]);
    expect(nodeMeta(node(nodes, LIGHT))).toContain("joined by entity_id light.living_room");
    expect(nodeMeta(node(nodes, TV))).toContain("joined by event_id 9c41");
  });
});

describe("fetchThreadCandidates", () => {
  const empty: StreamPage = { entries: [], next_before: null };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads one page of every stream ending ten minutes after the anchor, and drops the anchor itself", async () => {
    const calls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        calls.push(url);
        if (url.startsWith("/api/admin/streams/home_state?")) {
          return new Response(JSON.stringify({ entries: [TV.entry], next_before: null }), { status: 200 });
        }
        if (url.startsWith("/api/admin/streams/reflex_observations?")) {
          return new Response(JSON.stringify({ entries: [ANCHOR.entry], next_before: null }), { status: 200 });
        }
        if (url.startsWith("/api/admin/streams/events?")) {
          return new Response(JSON.stringify({ detail: "redis gone" }), { status: 503 });
        }
        return new Response(JSON.stringify(empty), { status: 200 });
      }),
    );

    const { candidates, searched } = await fetchThreadCandidates(ANCHOR);

    const before = `${T0 + 2000 + JOIN_WINDOW_MS + 1}-0`;
    expect(calls).toHaveLength(8);
    expect(calls[0]).toBe(`/api/admin/streams/user_requests?count=100&before=${before}`);
    expect(new Set(calls.map((url) => url.split("?")[1]))).toEqual(new Set([`count=100&before=${before}`]));
    expect(candidates).toEqual([TV]);
    expect(searched).toBe(7);
  });

  it("fails when no stream could be read, with the reason", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(JSON.stringify({ detail: "redis gone" }), { status: 503 })),
    );

    await expect(fetchThreadCandidates(ANCHOR)).rejects.toThrow("redis gone");
  });

  it("composes into a thread", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        const entries = url.startsWith("/api/admin/streams/home_state?")
          ? [TV.entry]
          : url.startsWith("/api/admin/streams/actions?")
            ? [ACTION.entry]
            : [];
        return new Response(JSON.stringify({ entries, next_before: null }), { status: 200 });
      }),
    );

    const thread = await fetchThread(ANCHOR);

    expect(ids(thread.nodes)).toEqual([TV.entry.id, ACTION.entry.id, ANCHOR.entry.id]);
    expect(thread.searched).toBe(8);
  });
});
