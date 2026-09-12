import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { idMs, STREAMS, type StreamRef } from "@/lib/streams";
import { CANDIDATE_COUNT } from "@/lib/trace";
import type { StreamEntry, StreamPage } from "@/lib/types";
import { reflexObservationsPage } from "@/test/fixtures";
import { WhySheet } from "./WhySheet";

// obs-1: the observation that dimmed the lights when the TV started (request 4b1d).
const ANCHOR: StreamRef = { stream: "reflex_observations", entry: reflexObservationsPage.entries[2] };
const ANCHOR_MS = idMs(ANCHOR.entry.id);

// obs-2: a different row, so re-asking has something else to find.
const OTHER: StreamRef = { stream: "reflex_observations", entry: reflexObservationsPage.entries[1] };
const OTHER_MS = idMs(OTHER.entry.id);

const ACTION: StreamEntry = {
  id: `${ANCHOR_MS - 1000}-0`,
  event: {
    event_id: "1f22b7c0",
    request_id: "4b1d",
    tool_name: "home.light_set",
    parameters: { entity_id: "light.living_room", brightness: 30 },
    target_service: "home-service",
    source: "reflex-engine",
  },
};
const RESULT: StreamEntry = {
  id: `${ANCHOR_MS - 500}-0`,
  event: { event_id: "c3d4e5f6", request_id: "4b1d", tool_name: "home.light_set", status: "success" },
};
// A second later, joined to nothing: the front door sensor.
const DOOR: StreamEntry = {
  id: `${ANCHOR_MS + 1000}-0`,
  event: {
    event_id: "7d80aa31",
    entity_id: "binary_sensor.front_door",
    old_state: "off",
    new_state: "on",
    domain: "home",
    source: "home-service",
  },
};
// obs-2's action, joined to it by request 8d2a.
const OTHER_ACTION: StreamEntry = {
  id: `${OTHER_MS - 1000}-0`,
  event: {
    event_id: "aa11bb22",
    request_id: "8d2a",
    tool_name: "home.fan_set",
    parameters: { entity_id: "fan.bathroom" },
    target_service: "home-service",
    source: "reflex-engine",
  },
};
// A full page that stops well inside the window: the stream answered, but its
// oldest entry is 299 s back and there is more behind `next_before`.
const BUSY: StreamEntry[] = Array.from({ length: CANDIDATE_COUNT }, (_, index) => ({
  id: `${ANCHOR_MS - 200_000 - index * 1000}-0`,
  event: {
    event_id: `busy${String(index).padStart(4, "0")}`,
    entity_id: `sensor.power_${index}`,
    old_state: "1",
    new_state: "2",
    domain: "sensor",
    source: "home-service",
  },
}));

/** Entries per stream, an HTTP status to fail that stream with, or a whole page. Unlisted streams are empty. */
let pages: Record<string, StreamEntry[] | number | StreamPage> = {};

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const name = String(input).replace("/api/admin/streams/", "").split("?")[0];
      const page = pages[name] ?? [];
      if (typeof page === "number") {
        return new Response(JSON.stringify({ detail: "redis gone" }), { status: page });
      }
      const body = Array.isArray(page) ? { entries: page, next_before: null } : page;
      return new Response(JSON.stringify(body), { status: 200 });
    }),
  );
}

function mount(anchor: StreamRef | null) {
  const onClose = vi.fn();
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const tree = (ref: StreamRef | null) => (
    <QueryClientProvider client={client}>
      <WhySheet anchor={ref} onClose={onClose} />
    </QueryClientProvider>
  );
  const view = render(tree(anchor));
  return { onClose, setAnchor: (ref: StreamRef | null) => view.rerender(tree(ref)) };
}

const FOOTNOTE = "searched 8 streams · 100 entries each · ±10 min";
const ALONE = "Nothing else in the eight streams is joined to this row.";

beforeEach(() => {
  pages = {};
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WhySheet", () => {
  it("renders nothing until a row asks, then explains the drawing and reads", async () => {
    const { setAnchor } = mount(null);
    expect(screen.queryByRole("dialog")).toBeNull();

    setAnchor(ANCHOR);
    expect(screen.getByRole("dialog", { name: "Why Alfred did that" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Every link drawn solid is joined by an id the server holds. Dashed means adjacent in time only.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("reading 8 streams…");

    await screen.findByText(FOOTNOTE);
    // The region stays mounted and empties, rather than unmounting: a region
    // inserted with its text already in it can go unannounced.
    expect(screen.getByRole("status")).toBeEmptyDOMElement();
  });

  it("draws the thread oldest first: solid into joined nodes, dashed into neighbours", async () => {
    pages = { actions: [ACTION], home_action_results: [RESULT], home_state: [DOOR] };
    mount(ANCHOR);
    await screen.findByText(FOOTNOTE);

    // Explicit, because WebKit drops the list role with the list-style
    // preflight strips (`gates/StepList.tsx`); jsdom would not notice.
    expect(screen.getByRole("list")).toHaveAttribute("role", "list");
    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.querySelector(".t-row")?.textContent)).toEqual([
      "home.light_set light.living_room",
      "home.light_set success",
      "observed media_player.tv · acted",
      "binary_sensor.front_door → on",
    ]);
    const metas = items.map((item) => item.querySelector(".t-meta-strong")?.textContent ?? "");
    expect(metas[0]).toContain("AC · request 4b1d · home-service · reflex-engine · joined by request_id 4b1d");
    expect(metas[1]).toContain("HR · request 4b1d · joined by request_id 4b1d");
    expect(metas[2]).toContain(
      'RX · home.light_set · request 4b1d · decision "movie started, evening, user home" · this row',
    );
    expect(metas[3]).toContain("HS · home · was off · via home-service · adjacent in time only");
    expect(screen.getAllByTestId("connector").map((node) => node.dataset.dashed)).toEqual([
      "false",
      "false",
      "true",
    ]);
    expect(screen.queryByText(ALONE)).toBeNull();
  });

  it("says when nothing else joined, rather than leaving a column of one unexplained", async () => {
    mount(ANCHOR);
    await screen.findByText(FOOTNOTE);

    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByText(ALONE)).toBeInTheDocument();
  });

  it("names each node's stream for a screen reader, since the monogram is a picture", async () => {
    pages = { actions: [ACTION] };
    mount(ANCHOR);
    await screen.findByText(FOOTNOTE);

    expect(screen.getAllByRole("listitem").map((item) => item.querySelector(".sr-only")?.textContent)).toEqual([
      "actions, ",
      "reflex observations, ",
    ]);
    // The tile itself is a picture and says nothing.
    expect(screen.getByText("AC")).toHaveAttribute("aria-hidden", "true");
  });

  it("says how many streams it could search, and why the rest are missing", async () => {
    // `6 of 8` alone left the two failures with no reason anywhere on screen:
    // the only other "could not be read" in the sheet is the *partial* suffix
    // below, which is about a read that succeeded and stopped short. Two
    // different things wearing one phrase across the seam.
    pages = { events: 503, notifications: 503 };
    mount(ANCHOR);
    await screen.findByText("searched 6 of 8 streams (2 could not be read) · 100 entries each · ±10 min");
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("counts a single unread stream in the singular", async () => {
    pages = { events: 503 };
    mount(ANCHOR);
    await screen.findByText("searched 7 of 8 streams (1 could not be read) · 100 entries each · ±10 min");
  });

  it("says when a stream could not be read back far enough", async () => {
    pages = { home_state: { entries: BUSY, next_before: `${ANCHOR_MS - 300_000}-0` } };
    mount(ANCHOR);
    await screen.findByText(`${FOOTNOTE} · 1 stream could not be read back far enough`);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
  });

  it("reports when no stream could be read", async () => {
    pages = Object.fromEntries(STREAMS.map((name) => [name, 503]));
    mount(ANCHOR);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("redis gone"));
    expect(screen.queryByRole("list")).toBeNull();
  });

  it("re-reads when a second row asks while the sheet is open", async () => {
    pages = { actions: [ACTION] };
    const { setAnchor } = mount(ANCHOR);
    await screen.findByText("home.light_set light.living_room");

    pages = { actions: [OTHER_ACTION] };
    setAnchor(OTHER);
    await screen.findByText("home.fan_set fan.bathroom");
    expect(screen.queryByText("home.light_set light.living_room")).toBeNull();
  });

  it("does not re-read eight streams when the same row asks again", async () => {
    pages = { actions: [ACTION] };
    const { setAnchor } = mount(ANCHOR);
    await screen.findByText(FOOTNOTE);
    const reads = vi.mocked(fetch).mock.calls.length;
    expect(reads).toBe(STREAMS.length);

    setAnchor(null);
    setAnchor(ANCHOR);
    await screen.findByText(FOOTNOTE);
    expect(vi.mocked(fetch).mock.calls).toHaveLength(reads);
  });

  it("keeps the thread on screen while it leaves", async () => {
    pages = { actions: [ACTION] };
    const { setAnchor } = mount(ANCHOR);
    await screen.findByText("home.light_set light.living_room");

    setAnchor(null);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText("home.light_set light.living_room")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
