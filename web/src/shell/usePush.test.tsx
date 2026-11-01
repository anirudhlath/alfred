import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  permission,
  reachContext,
  requestAndSubscribe,
  syncSubscription,
} from "@/lib/push";
import { BUSY, failedClient, makeClient } from "@/test/push-client";
import type { SyncAnswer } from "@/lib/push";
import { usePush, type PushOptions } from "./usePush";

vi.mock("@/lib/push", () => ({
  reachContext: vi.fn(),
  permission: vi.fn(),
  syncSubscription: vi.fn(),
  requestAndSubscribe: vi.fn(),
}));

/** The page's notification permission, which a test changes as the owner would. */
let granted: NotificationPermission;

function renderPush(client = makeClient(), options?: PushOptions) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { ...renderHook(() => usePush(options), { wrapper }), client };
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
 * A deadline as a browser rejects with one: an Error named TimeoutError, which
 * is all `timedOut` reads. Not `new DOMException(…)`, because jsdom's is not an
 * Error, and `errorText` would then say less than a phone does.
 */
const deadline = (message: string): Error =>
  Object.assign(new Error(message), { name: "TimeoutError" });

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
  vi.mocked(syncSubscription).mockResolvedValue("on");
  answers("granted");
});

afterEach(() => {
  // `comeBack` shadows jsdom's getter with an own property; deleting it restores it.
  Reflect.deleteProperty(document, "visibilityState");
});

describe("usePush", () => {
  it("is install in a tab", async () => {
    vi.mocked(reachContext).mockReturnValue("tab");
    const { result } = renderPush();
    await settle();

    expect(result.current.reach).toEqual({ kind: "install" });
    // Granted or not, a tab is never subscribed: iOS pushes only to the installed app.
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
    const sync = deferred<SyncAnswer>();
    vi.mocked(syncSubscription).mockReturnValue(sync.promise);
    const { result } = renderPush();

    await waitFor(() => expect(syncSubscription).toHaveBeenCalled());
    expect(result.current.reach).toEqual({ kind: "checking" });

    await act(async () => sync.resolve("on"));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
  });

  it("is failed with the error when the sync fails, and retry refetches", async () => {
    // `store_device()` gave up on a contended hash (decision 1): nothing was
    // stored, so the phone is not registered, and the way on is to ask again.
    vi.mocked(syncSubscription).mockRejectedValue(new ApiError(503, "Device store busy"));
    const { result } = renderPush();

    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "Device store busy" }),
    );
    // The app's one retry for a 5xx, and no more until someone asks.
    expect(syncSubscription).toHaveBeenCalledTimes(2);

    vi.mocked(syncSubscription).mockResolvedValue("on");
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(syncSubscription).toHaveBeenCalledTimes(3);
  });

  it("does not wait out a worker deadline twice", async () => {
    vi.mocked(syncSubscription).mockRejectedValue(deadline("no service worker"));
    const { result } = renderPush();

    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "no service worker" }),
    );
    // The retry policy never repeats a deadline failure: a sync that has already
    // waited ten seconds is not waited out again. The next try is `Try again`, or
    // the next sign-in, re-grant or reconnect (docs/web-push.md, "From the PWA").
    expect(syncSubscription).toHaveBeenCalledTimes(1);
  });

  it("is checking while a retry is under way, even after an earlier success", async () => {
    const { result, client } = renderPush();
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));

    // A sign-in's re-sync fails: the error stands beside the old `on`.
    vi.mocked(syncSubscription).mockRejectedValue(new ApiError(503, "Device store busy"));
    await act(async () => client.invalidateQueries());
    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "Device store busy" }),
    );

    const sync = deferred<SyncAnswer>();
    vi.mocked(syncSubscription).mockReturnValue(sync.promise);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "checking" }));

    await act(async () => sync.resolve("on"));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
  });

  it("is unconfigured when the house has no key", async () => {
    vi.mocked(syncSubscription).mockResolvedValue("unconfigured");
    const { result } = renderPush();

    await waitFor(() => expect(result.current.reach).toEqual({ kind: "unconfigured" }));
    expect(syncSubscription).toHaveBeenCalledTimes(1);
  });

  it("enables: asks, then syncs, and goes on", async () => {
    granted = "default";
    const asked = deferred<NotificationPermission>();
    vi.mocked(requestAndSubscribe).mockImplementation(() => asked.promise);
    const { result, rerender } = renderPush();
    expect(result.current.reach).toEqual({ kind: "ask" });
    expect(result.current.asking).toBe(false);

    act(() => result.current.enable());
    expect(result.current.asking).toBe(true);

    // The owner has said yes and `requestAndSubscribe` is still registering. A
    // render now reads the new permission, and must not start a second sync
    // beside the one Enable is running.
    granted = "granted";
    rerender();
    await settle();
    expect(syncSubscription).not.toHaveBeenCalled();

    await act(async () => asked.resolve("granted"));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(result.current.asking).toBe(false);
    // Enable's own registration is the only one: one register POST per tap.
    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    expect(syncSubscription).not.toHaveBeenCalled();
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
    // A sync after the 404 would fail: the 404 itself is the answer.
    vi.mocked(syncSubscription).mockRejectedValue(new ApiError(502, "Bad Gateway"));
    answers("granted", new ApiError(404, "Web Push not configured"));
    const { result } = renderPush();

    act(() => result.current.enable());
    await waitFor(() => expect(result.current.asking).toBe(false));
    expect(result.current.reach).toEqual({ kind: "unconfigured" });
    await settle();
    expect(syncSubscription).not.toHaveBeenCalled();
  });

  it("syncs once more when enabling fails another way", async () => {
    granted = "default";
    answers("granted", new ApiError(503, "Device store busy"));
    const { result } = renderPush();

    act(() => result.current.enable());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(result.current.asking).toBe(false);
    expect(syncSubscription).toHaveBeenCalledTimes(1);
  });

  it("re-reads the permission when the app comes back", async () => {
    const { result } = renderPush();
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));

    // Turned off in Settings while the app was in the background.
    granted = "denied";
    comeBack();
    expect(result.current.reach).toEqual({ kind: "denied" });
  });

  it("syncs again when a permission taken away is given back", async () => {
    const { result } = renderPush();
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
    expect(syncSubscription).toHaveBeenCalledTimes(1);

    granted = "denied";
    comeBack();
    expect(result.current.reach).toEqual({ kind: "denied" });

    // Back on in Settings. The browser deactivated the old subscription when the
    // permission went, so the `on` held from before is no longer true.
    granted = "granted";
    comeBack();
    await waitFor(() => expect(syncSubscription).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
  });

  it("syncs again on a re-grant when the permission went while a sync was in flight", async () => {
    const first = deferred<SyncAnswer>();
    vi.mocked(syncSubscription).mockReturnValueOnce(first.promise);
    const { result } = renderPush();
    await waitFor(() => expect(syncSubscription).toHaveBeenCalledTimes(1));

    granted = "denied";
    comeBack();
    expect(result.current.reach).toEqual({ kind: "denied" });

    // The register lands after the revoke. Its success describes a subscription
    // the browser has since deactivated, and must not stand in for a new sync.
    await act(async () => first.resolve("on"));
    await settle();

    granted = "granted";
    comeBack();
    await waitFor(() => expect(syncSubscription).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
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

describe("usePush on mount", () => {
  // `PushKeeper`'s: it remounts under `AuthGate` on each sign-in, and that
  // mount is the sync.
  it.each([false, true])(
    "syncs a failure again when a default observer mounts (after an on: %s)",
    async (afterOn) => {
      const { result } = renderPush(await failedClient(afterOn));

      await waitFor(() => expect(result.current.reach).toEqual({ kind: "on" }));
      expect(syncSubscription).toHaveBeenCalledTimes(1);
    },
  );

  // System › Reach's: the bench remounts on every visit, and opening it is not
  // asking again.
  it.each([false, true])(
    "leaves a failure standing when a syncOnMount: false observer mounts (after an on: %s)",
    async (afterOn) => {
      const { result } = renderPush(await failedClient(afterOn), { syncOnMount: false });
      await settle();

      expect(syncSubscription).not.toHaveBeenCalled();
      expect(result.current.reach).toEqual({ kind: "failed", detail: BUSY });
    },
  );
});
