import { afterEach, describe, expect, it, vi } from "vitest";
import {
  actionReducer,
  confirmAction,
  fetchAction,
  fetchPending,
  fuseRemaining,
  type TrackedAction,
} from "./actions";
import {
  actionResultFixture,
  pendingActionFixture,
  secondPendingActionFixture,
} from "@/test/fixtures";

const T0741 = Date.parse("2026-09-07T07:41:00Z");
const T0742 = Date.parse("2026-09-07T07:42:00Z");
const T0747 = Date.parse("2026-09-07T07:47:00Z");

const tracked = (phase: TrackedAction["phase"] = "pending"): TrackedAction => ({
  action: pendingActionFixture,
  phase,
});

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

  it("marks a 404 confirm as already answered", () => {
    const state = actionReducer([tracked()], { type: "confirm-404", id: "a91f3c2e" });
    expect(state[0].phase).toBe("answered");
  });

  it("only applies on the matching request id", () => {
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
