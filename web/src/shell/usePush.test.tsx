import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import {
  fetchPublicKey,
  permission,
  reachContext,
  requestAndSubscribe,
  syncSubscription,
} from "@/lib/push";
import { authSession } from "@/test/fixtures";
import { ReachSection } from "@/workshop/ReachSection";
import type { Sessions } from "@/workshop/useSystem";
import { QUERY_DEFAULTS } from "./QueryProvider";
import { PUSH_QUERY_KEY, usePush, type PushOptions } from "./usePush";

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
  vi.mocked(fetchPublicKey).mockResolvedValue(HOUSE_KEY);
  vi.mocked(syncSubscription).mockResolvedValue(undefined);
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
    // `store_device()` gave up on a contended hash (decision 1): nothing was
    // stored, so the phone is not registered, and the way on is to ask again.
    vi.mocked(syncSubscription).mockRejectedValue(new ApiError(503, "Device store busy"));
    const { result } = renderPush();

    await waitFor(() =>
      expect(result.current.reach).toEqual({ kind: "failed", detail: "Device store busy" }),
    );
    // The app's one retry for a 5xx, and no more until someone asks.
    expect(syncSubscription).toHaveBeenCalledTimes(2);

    vi.mocked(syncSubscription).mockResolvedValue(undefined);
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
    // A sync that has already waited ten seconds is not retried behind the
    // owner's back: the next try is theirs, from the card.
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

    const sync = deferred<undefined>();
    vi.mocked(syncSubscription).mockReturnValue(sync.promise);
    act(() => result.current.retry());
    await waitFor(() => expect(result.current.reach).toEqual({ kind: "checking" }));

    await act(async () => sync.resolve(undefined));
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
    expect(fetchPublicKey).not.toHaveBeenCalled();
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
    // A key read after the 404 would fail: the 404 itself is the answer.
    vi.mocked(fetchPublicKey).mockRejectedValue(new ApiError(502, "Bad Gateway"));
    answers("granted", new ApiError(404, "Web Push not configured"));
    const { result } = renderPush();

    act(() => result.current.enable());
    await waitFor(() => expect(result.current.asking).toBe(false));
    expect(result.current.reach).toEqual({ kind: "unconfigured" });
    await settle();
    expect(fetchPublicKey).not.toHaveBeenCalled();
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
    const first = deferred<undefined>();
    vi.mocked(syncSubscription).mockReturnValueOnce(first.promise);
    const { result } = renderPush();
    await waitFor(() => expect(syncSubscription).toHaveBeenCalledTimes(1));

    granted = "denied";
    comeBack();
    expect(result.current.reach).toEqual({ kind: "denied" });

    // The register lands after the revoke. Its success describes a subscription
    // the browser has since deactivated, and must not stand in for a new sync.
    await act(async () => first.resolve(undefined));
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

const BUSY = "Device store busy";

/**
 * A client already holding a sync that failed, as it is when System is opened
 * after one: `afterOn` puts an earlier `on` behind the failure, the shape a
 * sign-in's failed re-sync leaves. Either way react-query has marked the query
 * invalidated, which is what a mounting observer reads as a reason to fetch.
 */
async function failedClient(afterOn: boolean): Promise<QueryClient> {
  const client = makeClient();
  if (afterOn) client.setQueryData(PUSH_QUERY_KEY, "on");
  await client
    .fetchQuery({
      queryKey: PUSH_QUERY_KEY,
      queryFn: () => Promise.reject(new ApiError(503, BUSY)),
      retry: false,
      staleTime: 0,
    })
    .catch(() => {});
  expect(client.getQueryState(PUSH_QUERY_KEY)).toMatchObject({
    status: "error",
    isInvalidated: true,
    data: afterOn ? "on" : undefined,
  });
  return client;
}

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

      expect(fetchPublicKey).not.toHaveBeenCalled();
      expect(syncSubscription).not.toHaveBeenCalled();
      expect(result.current.reach).toEqual({ kind: "failed", detail: BUSY });
    },
  );
});

/**
 * The card over its own hook. `ReachSection.test.tsx` hands it a `PushValue`,
 * so it can only prove the row draws what it is given; whether opening the
 * bench syncs is a fact about the observer the card mounts.
 */
const SESSIONS: Sessions = {
  list: [authSession({ current: true })],
  read: true,
  ended: {},
  ending: {},
  failed: {},
  end: () => {},
  error: null,
};

const FAILED_SUB = "off · allowed in iOS · the house did not take the registration";

function renderReach(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <ReachSection sessions={SESSIONS} />
    </QueryClientProvider>,
  );
}

describe("ReachSection over usePush", () => {
  it.each([false, true])(
    "opens on a failed sync without syncing, and Try again syncs (after an on: %s)",
    async (afterOn) => {
      const user = userEvent.setup();
      renderReach(await failedClient(afterOn));
      await settle();

      expect(fetchPublicKey).not.toHaveBeenCalled();
      expect(syncSubscription).not.toHaveBeenCalled();
      expect(screen.getByText(FAILED_SUB)).toBeInTheDocument();
      expect(screen.getByText(BUSY)).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Try again" }));

      await waitFor(() => expect(screen.getByText("on")).toBeInTheDocument());
      expect(syncSubscription).toHaveBeenCalledTimes(1);
    },
  );

  it("enables from its own observer, registering once", async () => {
    granted = "default";
    const user = userEvent.setup();
    renderReach(makeClient());

    await user.click(screen.getByRole("button", { name: "Enable" }));
    await user.click(screen.getByRole("button", { name: "Let me reach you" }));

    await waitFor(() => expect(screen.getByText("on")).toBeInTheDocument());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(requestAndSubscribe).toHaveBeenCalledTimes(1);
    // Enable registered for itself; the query took its answer, and nothing
    // registered a second time.
    expect(fetchPublicKey).not.toHaveBeenCalled();
    expect(syncSubscription).not.toHaveBeenCalled();
  });
});
