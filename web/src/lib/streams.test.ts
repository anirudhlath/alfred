import { afterEach, describe, expect, it, vi } from "vitest";
import {
  compareIds,
  fetchStreamPage,
  idMs,
  isStreamName,
  STREAM_INFO,
  STREAMS,
  summarise,
} from "./streams";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("STREAMS", () => {
  it("lists the eight catalogued streams in the handoff's chip order", () => {
    expect(STREAMS).toEqual([
      "user_requests",
      "user_responses",
      "events",
      "actions",
      "reflex_observations",
      "notifications",
      "home_state",
      "home_action_results",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].mono)).toEqual([
      "UR", "AL", "EV", "AC", "RX", "NT", "HS", "HR",
    ]);
    expect(STREAMS.map((name) => STREAM_INFO[name].hue)).toEqual([
      30, 75, 120, 165, 210, 255, 300, 345,
    ]);
    expect(isStreamName("home_state")).toBe(true);
    expect(isStreamName("home-state")).toBe(false);
  });
});

describe("stream ids", () => {
  it("reads the millisecond half of an id, and 0 for garbage", () => {
    expect(idMs("1788815640000-0")).toBe(1788815640000);
    expect(idMs("garbage")).toBe(0);
  });

  it("orders by milliseconds, then sequence", () => {
    expect(compareIds("1788815640000-0", "1788815640000-1")).toBeLessThan(0);
    expect(compareIds("1788815640001-0", "1788815640000-9")).toBeGreaterThan(0);
    expect(compareIds("1788815640000-2", "1788815640000-2")).toBe(0);
  });
});

describe("fetchStreamPage", () => {
  it("asks for a page of fifty, before a cursor when given one", async () => {
    const fetchMock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify({ entries: [], next_before: null }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchStreamPage("events");
    await fetchStreamPage("events", "1788815640000-0");
    await fetchStreamPage("events", null, 100);

    expect(fetchMock.mock.calls.map(([input]) => String(input))).toEqual([
      "/api/admin/streams/events?count=50",
      "/api/admin/streams/events?count=50&before=1788815640000-0",
      "/api/admin/streams/events?count=100",
    ]);
  });

  it("normalises a page with nothing in it", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    expect(await fetchStreamPage("events")).toEqual({ entries: [], next_before: null });
  });
});

describe("summarise", () => {
  it("user_requests: the words, then session · channel · type", () => {
    expect(
      summarise("user_requests", {
        content: "Is the back door locked?",
        session_id: "s_9f3",
        channel: "web_pwa",
        content_type: "text",
      }),
    ).toEqual({ text: "Is the back door locked?", meta: "session s_9f3 · web_pwa · text" });
    expect(
      summarise("user_requests", { content: "", session_id: "s_9f3", channel: "voice", content_type: "audio" }),
    ).toEqual({ text: "voice message", meta: "session s_9f3 · voice · audio" });
  });

  it("user_responses: the reply, then session · mood · tools", () => {
    expect(
      summarise("user_responses", {
        text: "It is locked, sir.",
        session_id: "s_9f3",
        mood: "pleased",
        actions_taken: ["home.get_state", "weather.forecast"],
      }),
    ).toEqual({
      text: "It is locked, sir.",
      meta: "session s_9f3 · mood pleased · home.get_state, weather.forecast",
    });
    expect(summarise("user_responses", { text: "Certainly.", session_id: "s_9f3" })).toEqual({
      text: "Certainly.",
      meta: "session s_9f3 · mood neutral · no tools",
    });
  });

  it("events: trigger fired, trigger created, service registered, anything else", () => {
    expect(
      summarise("events", {
        event_type: "trigger_fired",
        trigger_name: "front door open",
        trigger_type: "sensor",
        urgency: "important",
        fired_by: "engine",
      }),
    ).toEqual({ text: "trigger fired: front door open", meta: "fired by engine · sensor · important" });
    expect(
      summarise("events", {
        event_type: "trigger_created",
        name: "bins out",
        trigger_type: "time",
        created_by: "conscious",
        one_shot: true,
      }),
    ).toEqual({ text: "trigger created: bins out", meta: "one-shot · time · by conscious" });
    expect(
      summarise("events", { event_type: "service_registered", service_name: "home-service", source: "home-service" }),
    ).toEqual({ text: "service registered: home-service", meta: "source home-service" });
    expect(summarise("events", { event_type: "state_changed", source: "bus" })).toEqual({
      text: "state changed",
      meta: "source bus",
    });
  });

  it("actions: tool and entity, then request · service · source · confirmed", () => {
    expect(
      summarise("actions", {
        tool_name: "home.light_set",
        parameters: { entity_id: "light.living_room", brightness: 30 },
        request_id: "4b1d9e00",
        target_service: "home-service",
        source: "reflex",
        confirmed: false,
      }),
    ).toEqual({
      text: "home.light_set light.living_room",
      meta: "request 4b1d · home-service · reflex",
    });
    expect(
      summarise("actions", {
        tool_name: "home.lock_unlock",
        parameters: {},
        request_id: "a91f3c2e",
        target_service: "home-service",
        source: "conscious",
        confirmed: true,
      }),
    ).toEqual({ text: "home.lock_unlock", meta: "request a91f · home-service · conscious · confirmed" });
  });

  it("reflex_observations: what was seen and whether it acted, then tool · request · decision", () => {
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "media_player.tv", new_state: "playing" },
        action: { request_id: "4b1d9e00", tool_name: "home.light_set" },
        result: { status: "success" },
        decision_context: "movie started, evening, user home",
      }),
    ).toEqual({
      text: "observed media_player.tv · acted",
      meta: 'home.light_set · request 4b1d · decision "movie started, evening, user home"',
    });
    expect(
      summarise("reflex_observations", {
        origin: "state_change",
        trigger_event: { entity_id: "fan.bathroom", new_state: "on" },
        action: { request_id: "8d2a0000", tool_name: "home.fan_set" },
        result: { status: "error", error: "service unavailable" },
        decision_context: null,
      }),
    ).toEqual({ text: "observed fan.bathroom · acted", meta: "home.fan_set · request 8d2a · failed" });
    expect(
      summarise("reflex_observations", {
        origin: "trigger_fired",
        trigger_event: { trigger_name: "front door open" },
        action: null,
        result: null,
        decision_context: "user moving about, lights already on",
      }),
    ).toEqual({
      text: "observed front door open · watched, took no action",
      meta: 'decision "user moving about, lights already on"',
    });
  });

  it("notifications: the body, then urgency · source · confirmation", () => {
    expect(
      summarise("notifications", {
        title: "Your parcel arrived",
        body: "The door sensor saw it at 18:20.",
        urgency: "important",
        source: "trigger-engine",
        metadata: {},
      }),
    ).toEqual({ text: "The door sensor saw it at 18:20.", meta: "important · trigger-engine" });
    expect(
      summarise("notifications", {
        title: "Confirmation required",
        body: "",
        urgency: "urgent",
        source: "domain-router",
        metadata: { pending_action_id: "a91f3c2e" },
      }),
    ).toEqual({ text: "Confirmation required", meta: "urgent · domain-router · confirmation a91f" });
  });

  it("home_state: entity → state, then domain · was · via", () => {
    expect(
      summarise("home_state", {
        entity_id: "binary_sensor.front_door",
        new_state: "on",
        old_state: "off",
        domain: "home",
        source: "home-service",
      }),
    ).toEqual({ text: "binary_sensor.front_door → on", meta: "home · was off · via home-service" });
    expect(
      summarise("home_state", { entity_id: "light.hall", new_state: "off", domain: "home", source: "home-service" }),
    ).toEqual({ text: "light.hall → off", meta: "home · was unknown · via home-service" });
  });

  it("home_action_results: tool and status, then request · error", () => {
    expect(
      summarise("home_action_results", { tool_name: "home.light_set", status: "success", request_id: "4b1d9e00" }),
    ).toEqual({ text: "home.light_set success", meta: "request 4b1d" });
    expect(
      summarise("home_action_results", {
        tool_name: "home.fan_set",
        status: "error",
        request_id: "8d2a0000",
        error: "service unavailable",
      }),
    ).toEqual({ text: "home.fan_set error", meta: "request 8d2a · service unavailable" });
  });

  it("never throws on an event with none of the fields", () => {
    for (const stream of STREAMS) {
      const { text, meta } = summarise(stream, {});
      expect(text.length).toBeGreaterThan(0);
      expect(typeof meta).toBe("string");
    }
  });
});
