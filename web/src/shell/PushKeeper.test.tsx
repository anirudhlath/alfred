import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { syncSubscription } from "@/lib/push";
import { QUERY_DEFAULTS } from "./QueryProvider";
import { NotificationOpen, PushKeeper } from "./PushKeeper";

const { HOUSE_KEY } = vi.hoisted(() => ({ HOUSE_KEY: "BFakeHouseKey" }));

// The installed app, permission granted, a house with a key. `onOpenPath` is the
// real one, so the route test hears a message the way the app does.
vi.mock("@/lib/push", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/push")>()),
  reachContext: vi.fn(() => "capable"),
  permission: vi.fn(() => "granted"),
  fetchPublicKey: vi.fn(() => Promise.resolve(HOUSE_KEY)),
  syncSubscription: vi.fn(() => Promise.resolve()),
}));

function Where() {
  return <span data-testid="path">{useLocation().pathname}</span>;
}

afterEach(() => {
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("PushKeeper", () => {
  it("syncs on launch when permission is granted", async () => {
    const client = new QueryClient({ defaultOptions: QUERY_DEFAULTS });
    const { container } = render(
      <QueryClientProvider client={client}>
        <PushKeeper />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(syncSubscription).toHaveBeenCalledWith(HOUSE_KEY));
    expect(container).toBeEmptyDOMElement();
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
