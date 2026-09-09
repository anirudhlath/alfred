import { afterEach, describe, expect, it, vi } from "vitest";
import {
  actionReducer,
  confirmAction,
  fetchAction,
  fetchPending,
  fuseLength,
  fusePercent,
  fuseRemaining,
  tombstoneItems,
  type TrackedAction,
} from "./actions";
import { hhmm } from "./format";
import {
  actionResultFixture,
  pendingActionFixture,
  secondPendingActionFixture,
} from "@/test/fixtures";

const T0741 = Date.parse("2026-09-07T07:41:00Z");
const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0747 = Date.parse("2026-09-07T07:47:00Z");
const T0750 = Date.parse("2026-09-07T07:50:00Z");

const tracked = (phase: TrackedAction["phase"] = "pending"): TrackedAction => ({
  action: pendingActionFixture,
  phase,
});

/** Two live approvals, oldest first — for pinning that an event names one id. */
const pair = (secondPhase: TrackedAction["phase"] = "pending"): TrackedAction[] => [
  tracked(),
  { action: secondPendingActionFixture, phase: secondPhase },
];

afterEach(() => vi.unstubAllGlobals());

describe("fuseRemaining", () => {
  it("counts the seconds left on the server's own clock", () => {
    expect(fuseRemaining(pendingActionFixture, T0742)).toBe(240);
    expect(fuseRemaining(pendingActionFixture, T0741)).toBe(300);
  });

  it("never counts below zero", () => {
    expect(fuseRemaining(pendingActionFixture, T0747)).toBe(0);
  });

  it("treats an unreadable expiry as lapsed rather than infinite", () => {
    expect(fuseRemaining({ ...pendingActionFixture, expires_at: "soon" }, T0742)).toBe(0);
  });
});

describe("fuseLength", () => {
  it("is the span between asked and expiry, not ttl_seconds", () => {
    expect(fuseLength(pendingActionFixture)).toBe(300);
    // The server reports what is *left* at read time; the fuse is still 300 s long.
    expect(fuseLength({ ...pendingActionFixture, ttl_seconds: 120 })).toBe(300);
  });

  it("falls back to ttl_seconds when a clock will not parse or runs backwards", () => {
    expect(fuseLength({ ...pendingActionFixture, timestamp: "earlier" })).toBe(300);
    expect(fuseLength({ ...pendingActionFixture, expires_at: "soon" })).toBe(300);
    expect(
      fuseLength({ ...pendingActionFixture, expires_at: pendingActionFixture.timestamp, ttl_seconds: 7 }),
    ).toBe(7);
  });
});

describe("fusePercent", () => {
  it("is the remaining share of the whole fuse, whatever the read said was left", () => {
    expect(fusePercent(pendingActionFixture, T0742)).toBe(80);
    expect(fusePercent({ ...pendingActionFixture, ttl_seconds: 120 }, T0742)).toBe(80);
    expect(fusePercent(pendingActionFixture, T0747)).toBe(0);
  });

  it("never draws more than a full ring when the phone's clock is behind", () => {
    expect(fusePercent(pendingActionFixture, T0741 - 60_000)).toBe(100);
  });

  it("survives a fuse with no length at all", () => {
    expect(
      fusePercent(
        { ...pendingActionFixture, expires_at: pendingActionFixture.timestamp, ttl_seconds: 0 },
        T0742,
      ),
    ).toBe(0);
  });
});

describe("actionReducer — loaded", () => {
  it("adopts unknown actions as pending, oldest first", () => {
    const state = actionReducer([], {
      type: "loaded",
      actions: [secondPendingActionFixture, pendingActionFixture],
    });
    expect(state.map((item) => item.action.request_id)).toEqual(["a91f3c2e", "7c2e0b1d"]);
    expect(state.every((item) => item.phase === "pending")).toBe(true);
  });

  it("keeps the phase of an id it already knows", () => {
    const state = actionReducer([tracked("queued")], {
      type: "loaded",
      actions: [pendingActionFixture],
    });
    expect(state[0].phase).toBe("queued");
  });

  it("leaves an action the list did not mention alone", () => {
    // A deep-link read and this list read race on a cold launch. Demoting here
    // would turn a live approval into a tombstone because two requests landed
    // out of order; a genuinely consumed one resolves through `tick`, a 404
    // confirm, or a result frame.
    const state = actionReducer([tracked()], { type: "loaded", actions: [] });
    expect(state).toHaveLength(1);
    expect(state[0].phase).toBe("pending");
  });

  it("leaves a tombstone alone", () => {
    const state = actionReducer([tracked("expired")], { type: "loaded", actions: [] });
    expect(state[0].phase).toBe("expired");
  });

  it("refreshes the payload of a known id", () => {
    const state = actionReducer([tracked()], {
      type: "loaded",
      actions: [{ ...pendingActionFixture, ttl_seconds: 120 }],
    });
    expect(state[0].action.ttl_seconds).toBe(120);
  });
});

describe("actionReducer — arrived", () => {
  it("adds a new action in age order", () => {
    const state = actionReducer([{ action: secondPendingActionFixture, phase: "pending" }], {
      type: "arrived",
      action: pendingActionFixture,
    });
    expect(state.map((item) => item.action.request_id)).toEqual(["a91f3c2e", "7c2e0b1d"]);
  });

  it("refreshes an action it already tracks without resetting its phase", () => {
    const state = actionReducer([tracked("queued")], {
      type: "arrived",
      action: { ...pendingActionFixture, ttl_seconds: 60 },
    });
    expect(state).toHaveLength(1);
    expect(state[0].phase).toBe("queued");
    expect(state[0].action.ttl_seconds).toBe(60);
  });
});

describe("actionReducer — answering", () => {
  it("queues on a confirm, and stamps when", () => {
    const state = actionReducer([tracked()], {
      type: "confirm-sent",
      id: "a91f3c2e",
      at: "2026-09-07T07:42:00Z",
    });
    expect(state[0].phase).toBe("queued");
    expect(state[0].confirmedAt).toBe("2026-09-07T07:42:00Z");
  });

  it("queues only the id the confirm named", () => {
    const state = actionReducer(pair(), {
      type: "confirm-sent",
      id: "7c2e0b1d",
      at: "2026-09-07T07:45:00Z",
    });
    expect(state.map((item) => item.phase)).toEqual(["pending", "queued"]);
  });

  it("still takes a confirm the phone had already given up on", () => {
    // The phone's clock ran ahead of the server's; the server took the answer.
    const state = actionReducer([tracked("expired")], {
      type: "confirm-sent",
      id: "a91f3c2e",
      at: "2026-09-07T07:46:00Z",
    });
    expect(state[0].phase).toBe("queued");
  });

  it("does not let a slow confirm walk an applied result back to queued", () => {
    const before = [{ ...tracked("applied"), appliedAt: "2026-09-07T07:42:10Z" }];
    const state = actionReducer(before, {
      type: "confirm-sent",
      id: "a91f3c2e",
      at: "2026-09-07T07:42:11Z",
    });
    expect(state[0].phase).toBe("applied");
    expect(state[0].confirmedAt).toBeUndefined();
  });

  it("marks a 404 confirm as already answered", () => {
    const state = actionReducer([tracked()], { type: "confirm-404", id: "a91f3c2e" });
    expect(state[0].phase).toBe("answered");
  });

  it("answers only the id the 404 named", () => {
    const state = actionReducer(pair(), { type: "confirm-404", id: "7c2e0b1d" });
    expect(state.map((item) => item.phase)).toEqual(["pending", "answered"]);
  });

  it.each(["queued", "applied", "expired"] as const)(
    "does not let a 404 unsay a %s approval",
    (phase) => {
      // Our own second confirm 404s too; and a fuse the phone already called
      // lapsed has run out on the server as well, which is the tombstone it has.
      const state = actionReducer([tracked(phase)], { type: "confirm-404", id: "a91f3c2e" });
      expect(state[0].phase).toBe(phase);
    },
  );

  it("applies the result and stamps when", () => {
    const state = actionReducer([tracked("queued")], {
      type: "result",
      result: actionResultFixture,
      at: "2026-09-07T07:42:10Z",
    });
    expect(state[0].phase).toBe("applied");
    expect(state[0].appliedAt).toBe("2026-09-07T07:42:10Z");
    expect(state[0].result).toEqual(actionResultFixture);
  });

  it("ignores a result for something it is not tracking", () => {
    const before = [tracked("queued")];
    const state = actionReducer(before, {
      type: "result",
      result: { ...actionResultFixture, request_id: "0000" },
      at: "2026-09-07T07:42:10Z",
    });
    expect(state[0].phase).toBe("queued");
  });

  it("believes a result even after the fuse lapsed — it really did happen", () => {
    const state = actionReducer([tracked("expired")], {
      type: "result",
      result: actionResultFixture,
      at: "2026-09-07T07:47:10Z",
    });
    expect(state[0].phase).toBe("applied");
  });
});

describe("actionReducer — tick", () => {
  it("expires a pending action whose fuse has run out", () => {
    const state = actionReducer([tracked()], { type: "tick", now: T0747 });
    expect(state[0].phase).toBe("expired");
  });

  it("leaves a queued action alone — its result may still be coming", () => {
    const state = actionReducer([tracked("queued")], { type: "tick", now: T0747 });
    expect(state[0].phase).toBe("queued");
  });

  it("does nothing at all while the fuse is running", () => {
    const before = [tracked()];
    expect(actionReducer(before, { type: "tick", now: T0742 })).toBe(before);
  });

  it("expires the lapsed pending one and leaves the lapsed queued one", () => {
    const state = actionReducer(pair("queued"), { type: "tick", now: T0750 });
    expect(state.map((item) => item.phase)).toEqual(["expired", "queued"]);
  });
});

describe("the three reads", () => {
  it("lists the pending actions", async () => {
    const mock = vi.fn<typeof fetch>(
      async () =>
        new Response(JSON.stringify({ actions: [pendingActionFixture] }), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchPending()).resolves.toEqual([pendingActionFixture]);
    expect(mock.mock.calls[0][0]).toBe("/api/actions/pending");
  });

  it("survives a body with no actions key", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("{}", { status: 200 })),
    );
    await expect(fetchPending()).resolves.toEqual([]);
  });

  it("reads one action by id", async () => {
    const mock = vi.fn<typeof fetch>(
      async () => new Response(JSON.stringify(pendingActionFixture), { status: 200 }),
    );
    vi.stubGlobal("fetch", mock);

    await expect(fetchAction("a91f3c2e")).resolves.toEqual(pendingActionFixture);
    expect(mock.mock.calls[0][0]).toBe("/api/actions/a91f3c2e");
  });

  it("posts a confirmation", async () => {
    const mock = vi.fn<typeof fetch>(async () => new Response('{"status":"confirmed"}', { status: 200 }));
    vi.stubGlobal("fetch", mock);

    await confirmAction("a91f3c2e");

    expect(mock.mock.calls[0][0]).toBe("/api/actions/a91f3c2e/confirm");
    expect((mock.mock.calls[0][1] as RequestInit).method).toBe("POST");
  });

  it("throws the 404 through so the caller can tombstone it", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response('{"detail":"Pending action not found or expired"}', { status: 404 })),
    );
    await expect(confirmAction("a91f3c2e")).rejects.toMatchObject({ status: 404 });
  });
});

describe("tombstoneItems", () => {
  it("says an expired approval was not done, and when it was asked", () => {
    const [item] = tombstoneItems([tracked("expired")]);
    expect(item).toMatchObject({
      kind: "tombstone",
      id: "tomb:a91f3c2e",
      at: "2026-09-07T07:46:00Z",
      title: "Lock unlock",
    });
    expect(item.kind === "tombstone" && item.meta).toBe(
      `expired ${hhmm("2026-09-07T07:46:00Z")} · not done · asked ${hhmm("2026-09-07T07:41:00Z")}`,
    );
  });

  it("says an already-answered one differently", () => {
    const [item] = tombstoneItems([tracked("answered")]);
    expect(item.kind === "tombstone" && item.meta).toBe(
      `already answered · asked ${hhmm("2026-09-07T07:41:00Z")}`,
    );
  });

  it("leaves live, queued and applied approvals out of the thread", () => {
    expect(tombstoneItems([tracked("pending"), tracked("queued"), tracked("applied")])).toEqual([]);
  });

  it("returns one row per action", () => {
    const items = tombstoneItems([
      tracked("expired"),
      { action: secondPendingActionFixture, phase: "answered" },
    ]);
    expect(items.map((item) => item.id)).toEqual(["tomb:a91f3c2e", "tomb:7c2e0b1d"]);
  });

  it("is empty for an empty store", () => {
    expect(tombstoneItems([])).toEqual([]);
  });
});
