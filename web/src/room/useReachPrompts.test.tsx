import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, render, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TrackedAction } from "@/lib/actions";
import { fetchPublicKey, permission, reachContext } from "@/lib/push";
import { gateAnswered, markGateAnswered, readAnsweredDoors } from "@/lib/reach-local";
import { Layer } from "@/shell/Layer";
import { Sheet } from "@/shell/Sheet";
import { IPHONE_UA, pendingActionFixture } from "@/test/fixtures";
import { makeClient } from "@/test/push-client";
import { PUSH_KEY_QUERY_KEY, useReachPrompts } from "./useReachPrompts";

vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  fetchPublicKey: vi.fn(),
}));

const MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15";
const KEY = "BHouseKey";
const NOW = Date.parse("2026-09-07T07:42:00Z");

/** What the Room hands the hook on a render; left out, what the launch began with. */
interface Doors {
  pending?: TrackedAction[];
  now?: number;
}

/** A launch: the hook on a client of its own, as a reload starts with an empty cache. */
function launch(pending: TrackedAction[] = []) {
  const client = makeClient();
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return {
    ...renderHook(
      ({ pending: doors = pending, now = NOW }: Doors = {}) => useReachPrompts(doors, now),
      { wrapper },
    ),
    client,
  };
}

/** A Door waiting on the owner. */
function door(id: string, timestamp: string): TrackedAction {
  return { action: { ...pendingActionFixture, request_id: id, timestamp }, phase: "pending" };
}

function on(userAgent: string): void {
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(userAgent);
}

/** Let what is queued land: react-query tells its observers from a timer. */
const settle = () =>
  act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });

/** The key read has answered, and the hook has drawn what it said. */
async function keyAnswered(client: QueryClient, status: "success" | "error" = "success") {
  await waitFor(() => expect(client.getQueryState(PUSH_KEY_QUERY_KEY)?.status).toBe(status));
  await settle();
}

/** The key read stays out until the test answers it. */
function holdKey(): (key: string) => void {
  let answer: (key: string) => void = () => {};
  vi.mocked(fetchPublicKey).mockReturnValue(
    new Promise((resolve) => {
      answer = resolve;
    }),
  );
  return (key) => answer(key);
}

/** A sign-in's unfiltered invalidate (`AuthGate`), which re-reads every active query. */
async function signIn(client: QueryClient) {
  await act(async () => {
    await client.invalidateQueries();
  });
  await settle();
}

beforeEach(() => {
  vi.resetAllMocks();
  on(IPHONE_UA);
  vi.mocked(reachContext).mockReturnValue("capable");
  vi.mocked(permission).mockReturnValue("default");
  vi.mocked(fetchPublicKey).mockResolvedValue(KEY);
});

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe("useReachPrompts' gate", () => {
  it("opens once the house's key is read", async () => {
    const answerKey = holdKey();
    const { result } = launch();

    await settle();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
    expect(result.current.gate).toBeNull();

    await act(async () => answerKey(KEY));

    await waitFor(() => expect(result.current.gate).toBe("standalone"));
  });

  it.each(["granted", "denied"] as const)(
    "does not open when iOS answered %s while the key was read",
    async (answer) => {
      const answerKey = holdKey();
      const { result, client, rerender } = launch();
      await settle();
      expect(fetchPublicKey).toHaveBeenCalledTimes(1);

      // Through Reach's own gate, which a slow read leaves time for.
      vi.mocked(permission).mockReturnValue(answer);
      await act(async () => answerKey(KEY));
      await keyAnswered(client);

      expect(result.current.gate).toBeNull();

      // The launch has had its one try, so a later render does not open it.
      vi.mocked(permission).mockReturnValue("default");
      rerender();
      expect(result.current.gate).toBeNull();
    },
  );

  it("does not open when a button was tapped while the key was read", async () => {
    const answerKey = holdKey();
    const { result, client, rerender } = launch();
    await settle();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);

    // Reach's own gate shares the mark.
    markGateAnswered();
    await act(async () => answerKey(KEY));
    await keyAnswered(client);

    expect(result.current.gate).toBeNull();

    // The launch has had its one try, so a later render does not open it.
    localStorage.clear();
    rerender();
    expect(result.current.gate).toBeNull();
  });

  it("does not open off an iPhone", async () => {
    on(MAC);
    const { result } = launch();

    await settle();

    expect(result.current.gate).toBeNull();
    expect(fetchPublicKey).not.toHaveBeenCalled();
  });

  it.each(["granted", "denied"] as const)("does not open for %s", async (answer) => {
    vi.mocked(permission).mockReturnValue(answer);
    const { result } = launch();

    await settle();

    expect(result.current.gate).toBeNull();
    expect(fetchPublicKey).not.toHaveBeenCalled();
  });

  it("does not open with no key", async () => {
    vi.mocked(fetchPublicKey).mockResolvedValue(null);
    const { result, client } = launch();

    await keyAnswered(client);

    expect(result.current.gate).toBeNull();
  });

  it("does not open in a tab", async () => {
    vi.mocked(reachContext).mockReturnValue("tab");
    const { result } = launch();

    await settle();

    expect(result.current.gate).toBeNull();
    expect(fetchPublicKey).not.toHaveBeenCalled();
  });

  it("skips a launch whose key read fails, and opens on the next", async () => {
    vi.mocked(fetchPublicKey).mockRejectedValue(new Error("no route to the house"));
    const first = launch();

    await keyAnswered(first.client, "error");

    expect(first.result.current.gate).toBeNull();
    // Once: the app's default would ask again, and this launch has been skipped.
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
    first.unmount();

    vi.mocked(fetchPublicKey).mockResolvedValue(KEY);
    const { result } = launch();

    await waitFor(() => expect(result.current.gate).toBe("standalone"));
  });

  it("stays skipped when a sign-in reads the key after a failure", async () => {
    vi.mocked(fetchPublicKey).mockRejectedValue(new Error("no route to the house"));
    const { result, client } = launch();
    await keyAnswered(client, "error");

    vi.mocked(fetchPublicKey).mockResolvedValue(KEY);
    await signIn(client);

    expect(client.getQueryData(PUSH_KEY_QUERY_KEY)).toBe(KEY);
    expect(result.current.gate).toBeNull();
  });

  it("does not open after either button", async () => {
    markGateAnswered();
    const { result } = launch();

    await settle();

    expect(result.current.gate).toBeNull();
    expect(fetchPublicKey).not.toHaveBeenCalled();
  });

  it("shows again after a launch with neither tap", async () => {
    const first = launch();
    await waitFor(() => expect(first.result.current.gate).toBe("standalone"));
    first.unmount();

    const { result } = launch();

    await waitFor(() => expect(result.current.gate).toBe("standalone"));
  });

  it("stays closed for this launch once closed", async () => {
    const { result, client } = launch();
    await waitFor(() => expect(result.current.gate).toBe("standalone"));

    act(() => result.current.closeGate());
    expect(result.current.gate).toBeNull();

    await signIn(client);

    // The key was read again, so the latch is what kept the gate shut.
    expect(fetchPublicKey).toHaveBeenCalledTimes(2);
    expect(result.current.gate).toBeNull();
  });

  describe("with something already up when the key answers", () => {
    /** A launch whose key read answers with `surface` up, `surface` mounted first. */
    async function answeredOver(surface: ReactNode) {
      const answerKey = holdKey();
      const up = render(surface);
      const first = launch();
      await settle();
      await act(async () => answerKey(KEY));
      await keyAnswered(first.client);
      return { up, first };
    }

    it.each([
      [
        "a layer",
        <Layer key="layer" open label="Critical approval">
          up
        </Layer>,
      ],
      [
        "a sheet",
        <Sheet key="sheet" open title="Held back" onClose={() => {}}>
          up
        </Sheet>,
      ],
    ])("skips this launch over %s, writing nothing, so the next one offers it", async (_, surface) => {
      const { up, first } = await answeredOver(surface);

      expect(first.result.current.gate).toBeNull();
      expect(gateAnswered()).toBe(false);
      first.unmount();
      up.unmount();

      const { result } = launch();

      await waitFor(() => expect(result.current.gate).toBe("standalone"));
    });

    it("stays skipped once the surface has gone, for the rest of this launch", async () => {
      const { up, first } = await answeredOver(
        <Layer open label="Critical approval">
          up
        </Layer>,
      );
      expect(first.result.current.gate).toBeNull();

      up.unmount();
      first.rerender();
      expect(first.result.current.gate).toBeNull();

      // The key is read again and says yes: the latch is what keeps the gate shut.
      await signIn(first.client);
      expect(fetchPublicKey).toHaveBeenCalledTimes(2);
      expect(first.result.current.gate).toBeNull();
    });

    it("skips over a surface still in its leave", async () => {
      const surface = (open: boolean) => (
        <Layer open={open} label="Critical approval">
          up
        </Layer>
      );
      const answerKey = holdKey();
      const up = render(surface(true));
      const { result, client } = launch();
      await settle();

      // Closed, and on screen until its 400 ms are up.
      up.rerender(surface(false));
      expect(document.querySelector(".rise-out")).not.toBeNull();
      await act(async () => answerKey(KEY));
      await keyAnswered(client);

      expect(result.current.gate).toBeNull();
    });

    it("opens when the surface has gone by the time the key answers", async () => {
      const answerKey = holdKey();
      const up = render(
        <Layer open label="Workshop">
          up
        </Layer>,
      );
      const { result, client } = launch();
      await settle();

      up.unmount();
      await act(async () => answerKey(KEY));
      await keyAnswered(client);

      expect(result.current.gate).toBe("standalone");
    });
  });

  it("holds a tap the store refused while this Room is mounted", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("The quota has been exceeded.", "QuotaExceededError");
    });
    const { result, client, rerender } = launch();
    await waitFor(() => expect(result.current.gate).toBe("standalone"));

    // As `ReachGates`' `Not yet` does.
    markGateAnswered();
    act(() => result.current.closeGate());
    expect(gateAnswered()).toBe(false);

    await signIn(client);
    rerender();

    expect(result.current.gate).toBeNull();
  });
});

describe("useReachPrompts' row", () => {
  const A = door("door-a", "2026-09-07T07:41:00Z");
  const B = door("door-b", "2026-09-07T07:44:00Z");

  /** The row is up: the key has been read and said yes. */
  async function shown(result: { current: { row: unknown } }) {
    await waitFor(() => expect(result.current.row).not.toBeNull());
  }

  beforeEach(() => {
    vi.mocked(reachContext).mockReturnValue("tab");
  });

  it("shows one row, at the Door's time, once the house's key is read", async () => {
    const { result } = launch([A]);

    await waitFor(() =>
      expect(result.current.row).toMatchObject({ kind: "reach", at: A.action.timestamp }),
    );
  });

  it("does not read the key until a Door waits", async () => {
    const { result, rerender } = launch();

    await settle();
    expect(fetchPublicKey).not.toHaveBeenCalled();
    expect(result.current.row).toBeNull();

    rerender({ pending: [A] });

    await shown(result);
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
  });

  it("no row while the read is pending", async () => {
    const answerKey = holdKey();
    const { result } = launch([A]);

    await settle();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
    expect(result.current.row).toBeNull();

    await act(async () => answerKey(KEY));
    await shown(result);
  });

  it("no row when the read fails", async () => {
    vi.mocked(fetchPublicKey).mockRejectedValue(new Error("no route to the house"));
    const { result, client } = launch([A]);

    await keyAnswered(client, "error");

    expect(result.current.row).toBeNull();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
  });

  it("reads a failed key again for the next Door, and not on a tick", async () => {
    vi.mocked(fetchPublicKey).mockRejectedValue(new Error("no route to the house"));
    const { result, client, rerender } = launch([A]);
    await keyAnswered(client, "error");

    rerender({ pending: [A], now: NOW + 1000 });
    await settle();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);

    vi.mocked(fetchPublicKey).mockResolvedValue(KEY);
    rerender({ pending: [] });
    rerender({ pending: [B] });

    await shown(result);
    expect(fetchPublicKey).toHaveBeenCalledTimes(2);
  });

  it("no row with no key", async () => {
    vi.mocked(fetchPublicKey).mockResolvedValue(null);
    const { result, client } = launch([A]);

    await keyAnswered(client);

    expect(result.current.row).toBeNull();
  });

  it("no row off an iPhone", async () => {
    on(MAC);
    const { result } = launch([A]);

    await settle();

    expect(result.current.row).toBeNull();
    expect(fetchPublicKey).not.toHaveBeenCalled();
  });

  it("no row in the installed app", async () => {
    vi.mocked(reachContext).mockReturnValue("capable");
    const { result } = launch([A]);

    // The gate needs the key too, so by now the key has answered yes.
    await waitFor(() => expect(result.current.gate).toBe("standalone"));

    expect(result.current.row).toBeNull();
  });

  it("leaves with the last Door", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);

    rerender({ pending: [] });

    expect(result.current.row).toBeNull();
  });

  it("one row for several Doors, at the oldest's time", async () => {
    const { result } = launch([A, B]);

    await shown(result);

    expect(result.current.row).toMatchObject({ at: A.action.timestamp });
  });

  it("Not now removes it, and that holds across a reload", async () => {
    const first = launch([A]);
    await shown(first.result);

    act(() => first.result.current.notNow());
    expect(first.result.current.row).toBeNull();
    first.unmount();

    const { result } = launch([A]);

    await settle();
    expect(result.current.row).toBeNull();
    // Nothing waits that `Not now` has not answered, so the key is not even asked for.
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
  });

  it("one tap answers every Door then waiting", async () => {
    const first = launch([A, B]);
    await shown(first.result);

    act(() => first.result.current.notNow());
    first.unmount();

    const { result } = launch([A, B]);

    await settle();
    expect(result.current.row).toBeNull();
    expect(fetchPublicKey).toHaveBeenCalledTimes(1);
  });

  it("answers the Doors waiting at the tap, not those of an earlier render", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);

    rerender({ pending: [A, B] });
    act(() => result.current.notNow());

    expect(result.current.row).toBeNull();
    expect(readAnsweredDoors()).toEqual(["door-a", "door-b"]);
  });

  it("replaces the answered list, so it cannot grow", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);
    act(() => result.current.notNow());

    rerender({ pending: [B] });
    await shown(result);
    act(() => result.current.notNow());

    expect(readAnsweredDoors()).toEqual(["door-b"]);
  });

  it("comes back with a new Door, at that Door's time", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);
    act(() => result.current.notNow());

    rerender({ pending: [A, B] });

    expect(result.current.row).toMatchObject({ at: B.action.timestamp });
  });

  it("comes back across a reload for the next Door, at that Door's time", async () => {
    const first = launch([A]);
    await shown(first.result);
    act(() => first.result.current.notNow());
    first.unmount();

    const { result } = launch([A, B]);
    await shown(result);

    expect(result.current.row).toMatchObject({ at: B.action.timestamp });
  });

  it("does not come back for a Door already waiting", async () => {
    const { result, rerender } = launch([A, B]);
    await shown(result);
    act(() => result.current.notNow());

    rerender({ pending: [B] });

    expect(result.current.row).toBeNull();
  });

  it("Not now holds for this run when the store throws", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new DOMException("The quota has been exceeded.", "QuotaExceededError");
    });
    const { result, rerender } = launch([A]);
    await shown(result);

    act(() => result.current.notNow());
    expect(result.current.row).toBeNull();
    expect(readAnsweredDoors()).toEqual([]);

    rerender({ pending: [A], now: NOW + 1000 });
    expect(result.current.row).toBeNull();
  });

  it("a Door with an unreadable time is placed at the Door clock", async () => {
    const { result } = launch([door("door-a", "garbage")]);

    await shown(result);

    expect(result.current.row).toMatchObject({ at: new Date(NOW).toISOString() });
  });

  it("a Door tick leaves the row and its buttons' callbacks as they were", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);
    const { row, notNow, showMe } = result.current;

    // `door.pending` is a new list on every tick, of the same Doors.
    rerender({ pending: [A], now: NOW + 1000 });

    expect(result.current.row).toBe(row);
    expect(result.current.notNow).toBe(notNow);
    expect(result.current.showMe).toBe(showMe);
  });

  it("the install gate stays open when the last Door leaves", async () => {
    const { result, rerender } = launch([A]);
    await shown(result);

    act(() => result.current.showMe());
    expect(result.current.gate).toBe("install");

    rerender({ pending: [] });

    expect(result.current.gate).toBe("install");
    expect(result.current.row).toBeNull();
  });
});
