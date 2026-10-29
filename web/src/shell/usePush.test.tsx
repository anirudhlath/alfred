import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  fetchPublicKey,
  permission,
  reachContext,
  requestAndSubscribe,
  syncSubscription,
} from "@/lib/push";
import { QUERY_DEFAULTS } from "./QueryProvider";
import { usePush } from "./usePush";

vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  fetchPublicKey: vi.fn(),
  syncSubscription: vi.fn(),
  requestAndSubscribe: vi.fn(),
}));

const HOUSE_KEY = "BFakeHouseKey";

/** The page's notification permission, which a test changes as the owner would. */
let granted: NotificationPermission;

/**
 * A client on the app's own policy, so the one retry a failed sync gets is the
 * source's and not the harness's. Only the backoff is the harness's.
 */
const makeClient = (): QueryClient =>
  new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });

function renderPush(client = makeClient()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => usePush(), { wrapper }), client };
}

/** A promise the test settles by hand, for what the hook says while one is in flight. */
function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

/**
 * The owner answers the prompt with `answer`, and the browser's permission
 * follows — a tick after the ask, as it does when someone taps the prompt.
 */
function answers(answer: NotificationPermission, failure?: Error): void {
  vi.mocked(requestAndSubscribe).mockImplementation(async () => {
    await Promise.resolve();
    granted = answer;
    if (failure) throw failure;
    return answer;
  });
}

/** The app returns to the foreground — from Settings, say. */
function comeBack(): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => void document.dispatchEvent(new Event("visibilitychange")));
}

/** Let every queued microtask run without asserting anything happened. */
const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  vi.resetAllMocks();
  granted = "granted";
  vi.mocked(reachContext).mockReturnValue("capable");
  vi.mocked(permission).mockImplementation(() => granted);
  vi.mocked(fetchPublicKey).mockResolvedValue(HOUSE_KEY);
  vi.mocked(syncSubscription).mockResolvedValue(undefined);
  answers("granted");
});

describe("usePush", () => {
  it("is install in a tab", async () => {
    vi.mocked(reachContext).mockReturnValue("tab");
    const { result } = renderPush();
    await settle();

    expect(result.current.reach).toEqual({ kind: "install" });
    // Granted or not, a tab is never subscribed: iOS pushes only to the installed app.
    expect(fetchPublicKey).not.toHaveBeenCalled();
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("is unavailable when the installed app has no push", async () => {
    vi.mocked(reachContext).mockReturnValue("unsupported");
    const { result } = renderPush();
    await settle();

    expect(result.current.reach).toEqual({ kind: "unavailable" });
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("is ask before permission, denied after a refusal", async () => {
    granted = "default";
    answers("denied");
    const { result } = renderPush();
    expect(result.current.reach).toEqual({ kind: "ask" });

    act(() => result.current.enable());
    expect(result.current.asking).toBe(true);
    await waitFor(() => expect(result.current.asking).toBe(false));
    expect(result.current.reach).toEqual({ kind: "denied" });
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("is checking while the sync is in flight, then on", async () => {
    const sync = deferred<undefined>();
    vi.mocked(syncSubscription).mockReturnValue(sync.promise);
    const { result } = renderPush();

    await waitFor(() => expect(syncSubscription).toHaveBeenCalledWith(HOUSE_KEY));
    expect(result.current.reach).toEqual({ kind: "checking" });

    await act(async () => sync.resolve(undefined));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
  });

  it("is failed with the error when the sync fails, and retry refetches", async () => {
    vi.mocked(syncSubscription).mockRejectedValue(new Error("no service worker"));
    const { result } = renderPush();

    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "no service worker" }),
    );
    // The app's one retry, and no more until someone asks.
    expect(syncSubscription).toHaveBeenCalledTimes(2);

    vi.mocked(syncSubscription).mockResolvedValue(undefined);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(syncSubscription).toHaveBeenCalledTimes(3);
  });

  it("is failed with the house's busy store, which retry asks again", async () => {
    // `store_device()` gave up on a contended hash: nothing was stored, so the
    // phone is not registered, and the way on is to ask again.
    vi.mocked(syncSubscription).mockRejectedValue(new ApiError(503, "Device store busy"));
    const { result } = renderPush();

    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "Device store busy" }),
    );

    vi.mocked(syncSubscription).mockResolvedValue(undefined);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
  });

  it("is unconfigured when the house has no key", async () => {
    vi.mocked(fetchPublicKey).mockResolvedValue(null);
    const { result } = renderPush();

    await waitFor(() => expect(result.current.reach).toEqual({ kind: "unconfigured" }));
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("enables: asks, then syncs, and goes on", async () => {
    granted = "default";
    const asked = deferred<NotificationPermission>();
    vi.mocked(requestAndSubscribe).mockImplementation(() => asked.promise);
    const { result } = renderPush();
    expect(result.current.reach).toEqual({ kind: "ask" });
    expect(result.current.asking).toBe(false);

    act(() => result.current.enable());
    expect(result.current.asking).toBe(true);

    granted = "granted";
    await act(async () => asked.resolve("granted"));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(result.current.asking).toBe(false);
    expect(syncSubscription).toHaveBeenCalledWith(HOUSE_KEY);
  });

  it("enables without awaiting before the permission request", () => {
    granted = "default";
    const { result } = renderPush();

    act(() => {
      result.current.enable();
      // Still inside the tap: nothing awaited, nothing rendered, no effect run.
      // iOS shows the prompt only from inside the gesture.
      expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    });
  });

  it("is unconfigured when enabling finds the house has no key", async () => {
    granted = "default";
    vi.mocked(fetchPublicKey).mockResolvedValue(null);
    answers("granted", new ApiError(404, "Web Push not configured"));
    const { result } = renderPush();

    act(() => result.current.enable());
    await waitFor(() => expect(result.current.asking).toBe(false));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "unconfigured" }));
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("re-reads the permission when the app comes back", async () => {
    const { result } = renderPush();
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));

    // Turned off in Settings while the app was in the background.
    granted = "denied";
    comeBack();
    expect(result.current.reach).toEqual({ kind: "denied" });
  });

  it("syncs again after every sign-in", async () => {
    const { result, client } = renderPush();
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(syncSubscription).toHaveBeenCalledTimes(1);

    // What `AuthGate`'s `refetchEverything` does on a sign-in.
    await act(async () => client.invalidateQueries());
    await waitFor(() => expect(syncSubscription).toHaveBeenCalledTimes(2));
    expect(result.current.reach).toEqual({ kind: "on" });
  });
});
