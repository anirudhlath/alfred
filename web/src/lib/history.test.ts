import { afterEach, describe, expect, it, vi } from "vitest";
import {
  fetchRoomHistory,
  pendingActionTitles,
  toTimelineItems,
  withDividers,
  type RoomHistory,
  type TimelineItem,
} from "./history";
import {
  notificationsPage,
  reflexObservationsPage,
  userRequestsPage,
  userResponsesPage,
  yesterdayRequestPage,
} from "@/test/fixtures";

const HISTORY: RoomHistory = {
  user_requests: userRequestsPage.entries,
  user_responses: userResponsesPage.entries,
  reflex_observations: reflexObservationsPage.entries,
  notifications: notificationsPage.entries,
};

afterEach(() => vi.unstubAllGlobals());

describe("fetchRoomHistory", () => {
  it("reads exactly the four Room streams, fifty each", async () => {
    const urls: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        urls.push(String(input));
        return new Response('{"entries":[],"next_before":null}', { status: 200 });
      }),
    );

    await fetchRoomHistory();

    expect(urls.sort()).toEqual([
      "/api/admin/streams/notifications?count=50",
      "/api/admin/streams/reflex_observations?count=50",
      "/api/admin/streams/user_requests?count=50",
      "/api/admin/streams/user_responses?count=50",
    ]);
  });

  it("loses one stream rather than the whole conversation", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes("notifications"))
          return new Response('{"detail":"redis is down"}', { status: 503 });
        return new Response(JSON.stringify(userRequestsPage), { status: 200 });
      }),
    );

    const history = await fetchRoomHistory();

    expect(history.notifications).toEqual([]);
    expect(history.user_requests).toHaveLength(2);
  });
});

describe("toTimelineItems", () => {
  const items = toTimelineItems(HISTORY);

  it("puts the whole thread in chronological order", () => {
    expect(items.map((item) => item.kind)).toEqual([
      "act",
      "act",
      "act",
      "you",
      "alfred",
      "you",
      "alfred",
    ]);
    const stamps = items.map((item) => Date.parse(item.at));
    expect(stamps).toEqual([...stamps].sort((a, b) => a - b));
  });

  it("renders what you said as a sent bubble", () => {
    const you = items.find((item) => item.kind === "you")!;
    expect(you).toMatchObject({
      kind: "you",
      text: "What have I got tomorrow morning?",
      state: "sent",
    });
  });

  it("carries mood and tools onto Alfred's row", () => {
    const alfred = items.find((item) => item.kind === "alfred")!;
    expect(alfred).toMatchObject({
      kind: "alfred",
      mood: "pleased",
      actions: ["calendar.today", "weather.forecast"],
    });
  });

  it("reads a reflex act as its decision, in the RX hue", () => {
    const act = items[0];
    expect(act).toMatchObject({
      kind: "act",
      hue: 210,
      text: "movie started, evening, user home",
      meta: "17:58 · reflex · home.light_set",
    });
  });

  it("says when a reflex act failed", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.meta).toBe("19:41 · reflex · home.fan_set · failed");
  });

  it("falls back to the tool name when there is no decision context", () => {
    const failed = items.find((item) => item.kind === "act" && item.meta.includes("home.fan_set"))!;
    expect(failed.kind === "act" && failed.text).toBe("home.fan_set");
  });

  it("drops the observations Alfred only watched", () => {
    expect(items.some((item) => item.kind === "act" && item.text.includes("moving about"))).toBe(
      false,
    );
  });

  it("renders a notification as its title, in the NT hue", () => {
    const nt = items.find((item) => item.kind === "act" && item.hue === 255)!;
    expect(nt).toMatchObject({
      text: "Your parcel arrived",
      meta: "18:20 · trigger:trg_parcel · important",
    });
  });

  it("leaves confirmation requests to the Door", () => {
    expect(
      items.some((item) => item.kind === "act" && item.text.includes("Confirmation required")),
    ).toBe(false);
  });

  it("gives every row a stable, unique id", () => {
    const ids = items.map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
    expect(toTimelineItems(HISTORY).map((item) => item.id)).toEqual(ids);
  });

  it("skips an entry whose event is unreadable rather than dying", () => {
    const parsed = toTimelineItems({
      user_requests: [{ id: "1-0", event: {} }, ...userRequestsPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });
    expect(parsed).toHaveLength(2);
  });
});

describe("withDividers", () => {
  const now = new Date(2026, 8, 7, 21, 30);

  it("opens each day with its own label", () => {
    const twoDays = toTimelineItems({
      user_requests: [...userRequestsPage.entries, ...yesterdayRequestPage.entries],
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    });

    const labels = withDividers(twoDays, now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    expect(labels).toEqual(["yesterday", "earlier today"]);
  });

  it("starts a new conversation after a thirty-minute silence", () => {
    const labels = withDividers(toTimelineItems(HISTORY), now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    // 20:52 then 21:14 is 22 minutes — the same conversation. The act rows before
    // them are not turns and never open one.
    expect(labels).toEqual(["earlier today"]);
  });

  it("splits two turns more than thirty minutes apart", () => {
    const first = toTimelineItems({
      user_requests: userRequestsPage.entries,
      user_responses: [],
      reflex_observations: [],
      notifications: [],
    })[0];
    const later: TimelineItem = {
      kind: "you",
      id: "you:later",
      at: "2026-09-07T21:44:00",
      text: "And the windows?",
      state: "sent",
    };

    const labels = withDividers([first, later], now)
      .filter((item) => item.kind === "divider")
      .map((item) => (item.kind === "divider" ? item.label : ""));

    expect(labels).toEqual(["earlier today", "new conversation · 21:44"]);
  });

  it("leaves an empty thread empty", () => {
    expect(withDividers([], now)).toEqual([]);
  });

  it("gives every divider a unique id", () => {
    const ids = withDividers(toTimelineItems(HISTORY), now).map((item) => item.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("pendingActionTitles", () => {
  it("names an approval by its tool, for the tombstone a deep link may need", () => {
    expect(pendingActionTitles(HISTORY)).toEqual({ a91f3c2e: "Lock unlock" });
  });

  it("is empty for an absent history", () => {
    expect(pendingActionTitles(undefined)).toEqual({});
  });
});
