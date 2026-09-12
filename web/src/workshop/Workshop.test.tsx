import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { StreamPage } from "@/lib/types";
import { ConnectionProvider } from "@/shell/ConnectionProvider";
import { overviewFixture, reflexObservationsPage } from "@/test/fixtures";
import { Workshop } from "./Workshop";

const sockets = vi.hoisted(() => ({ telemetryUp: true }));

vi.mock("@/lib/chat-socket", () => {
  class ChatSocket {
    onstatus: (status: string) => void = () => {};
    connect() { this.onstatus("online"); }
    close = vi.fn();
    sendText = vi.fn(() => true);
    sendAudio = vi.fn(() => true);
    listen() { return () => {}; }
  }
  return { ChatSocket };
});

vi.mock("@/lib/telemetry-socket", () => {
  class TelemetrySocket {
    onstatus: (status: string) => void = () => {};
    close = vi.fn();
    subscribe = vi.fn();
    unsubscribe = vi.fn();
    connect() { this.onstatus(sockets.telemetryUp ? "online" : "offline"); }
    listen(): () => void { return () => {}; }
  }
  return { TelemetrySocket };
});

const empty: StreamPage = { entries: [], next_before: null };

/** The acted observation on `media_player.tv`, found by what it is rather than where it sits. */
const observation = reflexObservationsPage.entries.find(
  (entry) => (entry.event as { observation_id?: string }).observation_id === "obs-1",
);

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/api/admin/overview") return new Response(JSON.stringify(overviewFixture), { status: 200 });
      if (url === "/api/admin/streams/reflex_observations?count=50") {
        return new Response(JSON.stringify(reflexObservationsPage), { status: 200 });
      }
      return new Response(JSON.stringify(empty), { status: 200 });
    }),
  );
}

function mount(open: boolean, onClose = vi.fn(), onWhy = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (isOpen: boolean) => (
    <QueryClientProvider client={client}>
      <ConnectionProvider>
        <Workshop open={isOpen} onClose={onClose} onWhy={onWhy} />
      </ConnectionProvider>
    </QueryClientProvider>
  );
  const view = render(tree(open));
  return { ...view, onClose, onWhy, reopen: (isOpen: boolean) => view.rerender(tree(isOpen)) };
}

beforeEach(() => {
  sockets.telemetryUp = true;
  vi.useFakeTimers({ shouldAdvanceTime: true });
  vi.setSystemTime(new Date(2026, 8, 7, 21, 14, 0));
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Workshop", () => {
  it("rises under the sheets as the Workshop, and Room closes it", () => {
    const { onClose } = mount(true);
    const dialog = screen.getByRole("dialog", { name: "Workshop" });
    expect(dialog.className).toContain("z-10");
    fireEvent.click(screen.getByRole("button", { name: "Room" }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("opens on Activity; the other benches say they are not built", () => {
    mount(true);
    expect(screen.getByRole("tab", { name: "Activity" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByText("not built yet · phase 3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Pause feed" })).toBeNull();
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    expect(screen.getByRole("button", { name: "Pause feed" })).toBeInTheDocument();
  });

  it("says live with the overview's rate", async () => {
    mount(true);
    await waitFor(() => expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · 2.1 ev/s"));
  });

  it("says paused with the held count, and last true when the socket is down", async () => {
    const { unmount } = mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("paused · 0 new");
    unmount();

    sockets.telemetryUp = false;
    mount(true);
    // The *feed's* stamp, not the connection's: the chat socket is up and has
    // set `lastTrueAt` to 21:14, but the pump has never been live, and the
    // header may not say a thing the banner below it contradicts.
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true --:-- · not live");
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent(
      "Feed has not been live yet. Nothing below is live.",
    );
    // Pause is honoured underneath, but the status line still says not live.
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(screen.getByTestId("workshop-status")).toHaveTextContent("last true --:-- · not live");
  });

  it("does not report a rate it has never read", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("nope", { status: 500 })));
    mount(true);
    // `evs({})` is a bare `0`, which would call a house it cannot reach silent.
    await waitFor(() => expect(screen.getByTestId("workshop-status")).toHaveTextContent("live · — ev/s"));
  });

  it("reads the streams once open and hands a reflex row's why up", async () => {
    const { onWhy } = mount(true);
    await screen.findByText("observed media_player.tv · acted");
    fireEvent.click(screen.getByText("observed media_player.tv · acted"));
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({ stream: "reflex_observations", entry: observation });
  });

  it("hangs the bench off its own tab", () => {
    mount(true);
    const panel = screen.getByRole("tabpanel");
    const activity = screen.getByRole("tab", { name: "Activity" });
    expect(activity).toHaveAttribute("aria-controls", panel.id);
    expect(panel).toHaveAttribute("aria-labelledby", activity.id);
    expect(panel).toContainElement(screen.getByRole("button", { name: "Pause feed" }));

    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    expect(screen.getByRole("tabpanel")).toHaveAttribute(
      "aria-labelledby",
      screen.getByRole("tab", { name: "Memory" }).id,
    );
  });

  it("keeps the Activity bench's state through a trip to another bench", () => {
    mount(true);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    fireEvent.click(screen.getByRole("tab", { name: "Memory" }));
    fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
    // Every hook lives in `WorkshopPanel`, above the bench that is swapped out,
    // which is the whole reason it is shaped that way: the hold survives, and
    // so do the rows behind it.
    expect(screen.getByRole("button", { name: "Resume" })).toBeInTheDocument();
  });

  it("closes on Escape, which is all a standalone app has", () => {
    const { onClose } = mount(true);
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("stays for its leave, then unmounts", () => {
    const { reopen } = mount(true);
    reopen(false);
    expect(screen.getByRole("dialog").className).toContain("rise-out");
    act(() => vi.advanceTimersByTime(400));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
