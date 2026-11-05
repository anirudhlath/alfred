import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearBadge } from "@/lib/badge";
import { reachContext, syncSubscription } from "@/lib/push";
import { QUERY_DEFAULTS } from "./QueryProvider";
import { NotificationOpen, PushKeeper } from "./PushKeeper";

// The installed app, permission granted, a house with a key. `onOpenPath` is the
// real one, so the route test hears a message the way the app does.
vi.mock("@/lib/push", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/push")>()),
  reachContext: vi.fn(() => "capable"),
  permission: vi.fn(() => "granted"),
  syncSubscription: vi.fn(() => Promise.resolve("on" as const)),
}));

// `onBadgeAdded` is the real one, for the same reason.
vi.mock("@/lib/badge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/badge")>()),
  clearBadge: vi.fn(() => Promise.resolve()),
}));

function Where() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

/** A mounted `PushKeeper`, once its launch sync has answered. */
async function keeper() {
  const client = new QueryClient({ defaultOptions: QUERY_DEFAULTS });
  const mounted = render(
    <QueryClientProvider client={client}>
      <PushKeeper />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(client.isFetching()).toBe(0));
  return mounted;
}

/** The worker's container, which posts `BADGE_ADDED` to the open window. */
function theWorker(): { added: () => void } {
  const worker = new EventTarget();
  Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: worker });
  return {
    added: () => {
      worker.dispatchEvent(new MessageEvent("message", { data: { type: "BADGE_ADDED" } }));
    },
  };
}

function setVisibility(state: DocumentVisibilityState): void {
  Object.defineProperty(document, "visibilityState", { configurable: true, value: state });
}

function returnToForeground(): void {
  setVisibility("visible");
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.mocked(clearBadge).mockClear();
  setVisibility("visible");
});

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
  Reflect.deleteProperty(document, "visibilityState");
  vi.mocked(reachContext).mockReset();
});

describe("PushKeeper", () => {
  it("syncs on launch when permission is granted", async () => {
    const client = new QueryClient({ defaultOptions: QUERY_DEFAULTS });
    const { container } = render(
      <QueryClientProvider client={client}>
        <PushKeeper />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(syncSubscription).toHaveBeenCalled());
    expect(container).toBeEmptyDOMElement();
  });

  it("clears on mount in the installed app", async () => {
    await keeper();
    expect(clearBadge).toHaveBeenCalledTimes(1);
  });

  it("clears on return to the foreground", async () => {
    await keeper();
    vi.mocked(clearBadge).mockClear();

    act(returnToForeground);
    expect(clearBadge).toHaveBeenCalledTimes(1);
  });

  it("clears on the worker's message while visible", async () => {
    const worker = theWorker();
    await keeper();
    vi.mocked(clearBadge).mockClear();

    worker.added();
    expect(clearBadge).toHaveBeenCalledTimes(1);
  });

  it("does not clear on the message while hidden", async () => {
    const worker = theWorker();
    await keeper();
    vi.mocked(clearBadge).mockClear();

    setVisibility("hidden");
    worker.added();
    expect(clearBadge).not.toHaveBeenCalled();
  });

  it("clears nothing in a tab", async () => {
    vi.mocked(reachContext).mockReturnValue("tab");
    const worker = theWorker();
    await keeper();

    act(returnToForeground);
    worker.added();
    expect(clearBadge).not.toHaveBeenCalled();
  });

  it("stops clearing once unmounted", async () => {
    const worker = theWorker();
    const { unmount } = await keeper();
    expect(clearBadge).toHaveBeenCalledTimes(1);
    unmount();
    vi.mocked(clearBadge).mockClear();

    returnToForeground();
    worker.added();
    expect(clearBadge).not.toHaveBeenCalled();
  });
});

describe("NotificationOpen", () => {
  it("routes an OPEN_PATH message", () => {
    // The worker's container, which posts a tap's path to the open window.
    const worker = new EventTarget();
    Object.defineProperty(navigator, "serviceWorker", { configurable: true, value: worker });
    render(
      <MemoryRouter initialEntries={["/"]}>
        <NotificationOpen />
        <Where />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/$/);

    act(() => {
      worker.dispatchEvent(
        new MessageEvent("message", { data: { type: "OPEN_PATH", path: "/actions/abc" } }),
      );
    });
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/actions\/abc$/);
  });
});
