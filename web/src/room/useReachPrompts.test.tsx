import { QueryClientProvider, type QueryClient } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fetchPublicKey, permission, reachContext } from "@/lib/push";
import { gateAnswered, markGateAnswered } from "@/lib/reach-local";
import { makeClient } from "@/test/push-client";
import { PUSH_KEY_QUERY_KEY, useReachPrompts } from "./useReachPrompts";

vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  fetchPublicKey: vi.fn(),
}));

const IPHONE =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 18_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.2 Mobile/15E148 Safari/604.1";
const MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15";
const KEY = "BHouseKey";

/** A launch: the hook on a client of its own, as a reload starts with an empty cache. */
function launch(client: QueryClient = makeClient()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => useReachPrompts(), { wrapper }), client };
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
  on(IPHONE);
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

  it("holds a tap the store refused for the rest of this launch", async () => {
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
