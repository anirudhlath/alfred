import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { STREAMS, type StreamName } from "@/lib/streams";
import { contrast, token } from "@/test/contrast";
import { reflexObservationsPage, userRequestsPage } from "@/test/fixtures";
import { ActivityBench } from "./ActivityBench";
import type { Activity } from "./useActivity";

const reflex: FeedRow = {
  stream: "reflex_observations",
  entry: reflexObservationsPage.entries[2],
  key: `reflex_observations:${reflexObservationsPage.entries[2].id}`,
};
const request: FeedRow = {
  stream: "user_requests",
  entry: userRequestsPage.entries[0],
  key: `user_requests:${userRequestsPage.entries[0].id}`,
};
/** One row older than both, for the `↑ older` append. */
const older: FeedRow = {
  stream: "user_requests",
  entry: userRequestsPage.entries[1],
  key: `user_requests:${userRequestsPage.entries[1].id}`,
};
/** One row newer than all three, for the live prepend. */
const newest: FeedRow = {
  stream: "reflex_observations",
  entry: reflexObservationsPage.entries[0],
  key: `reflex_observations:${reflexObservationsPage.entries[0].id}`,
};

function activity(overrides: Partial<Activity> = {}): Activity {
  return {
    rows: [reflex, request],
    counts: Object.fromEntries(STREAMS.map((name) => [name, 0])) as Record<StreamName, number>,
    live: true,
    paused: false,
    liveAt: 1,
    heldCount: 0,
    solo: null,
    setSolo: vi.fn(),
    expanded: null,
    toggle: vi.fn(),
    pause: vi.fn(),
    resume: vi.fn(),
    cursor: null,
    fetchingOlder: false,
    loadOlder: vi.fn(),
    loaded: true,
    error: null,
    ...overrides,
  };
}

/**
 * jsdom lays nothing out: `scrollHeight` is always 0 and `scrollTop` never
 * takes a value. Own properties over both, so a test can say how tall the list
 * is and read back where the bench scrolled it to.
 */
function stubScroll(el: HTMLElement, height: number) {
  let scrollHeight = height;
  let scrollTop = 0;
  Object.defineProperty(el, "scrollHeight", { configurable: true, get: () => scrollHeight });
  Object.defineProperty(el, "scrollTop", {
    configurable: true,
    get: () => scrollTop,
    set: (value: number) => {
      scrollTop = value;
    },
  });
  return {
    grow: (to: number) => {
      scrollHeight = to;
    },
    get top() {
      return scrollTop;
    },
    set top(value: number) {
      scrollTop = value;
    },
  };
}

describe("ActivityBench", () => {
  it("lists the rows with the older button on top when there is something older", () => {
    const a = activity({ cursor: "1788800280000-0" });
    render(<ActivityBench activity={a} onWhy={() => {}} />);
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    const older = within(items[0]).getByRole("button", { name: "Load older entries" });
    expect(older).toHaveAttribute("aria-disabled", "false");
    // Spoken as four words, shown as the cursor it will read before.
    expect(older).toHaveTextContent("↑ older · before cursor 1788800280000-0");
    fireEvent.click(older);
    expect(a.loadOlder).toHaveBeenCalledTimes(1);
    expect(within(items[1]).getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent("");
    expect(screen.getByRole("status", { name: "Read errors" })).toHaveTextContent("");
  });

  it("has no older button when nothing older exists", () => {
    render(<ActivityBench activity={activity()} onWhy={() => {}} />);
    expect(screen.queryByRole("button", { name: /older/ })).toBeNull();
  });

  it("says fetching, and waits, while an older page is on its way", () => {
    const a = activity({ cursor: "1788800280000-0", fetchingOlder: true });
    render(<ActivityBench activity={a} onWhy={() => {}} />);
    const older = screen.getByRole("button", { name: "Fetching older entries" });
    expect(older).toHaveAttribute("aria-disabled", "true");
    expect(older).toHaveTextContent("↑ older · fetching before cursor 1788800280000-0");
    // Still focusable, and `loadOlder` is what refuses the second read.
    expect(older).not.toBeDisabled();
  });

  it("says the feed stopped, and when, while the socket is down", () => {
    const at = new Date(2026, 8, 7, 21, 14, 0).getTime();
    const { rerender } = render(<ActivityBench activity={activity({ live: false, liveAt: at })} onWhy={() => {}} />);
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent(
      "Feed stopped at 21:14. Nothing below is live.",
    );
    rerender(<ActivityBench activity={activity({ live: false, liveAt: null })} onWhy={() => {}} />);
    expect(screen.getByRole("status", { name: "Feed status" })).toHaveTextContent(
      "Feed has not been live yet. Nothing below is live.",
    );
  });

  it("shows a read failure as a status line under the banner", () => {
    render(
      <ActivityBench activity={activity({ error: "2 of 8 streams could not be read · redis gone" })} onWhy={() => {}} />,
    );
    expect(screen.getByRole("status", { name: "Read errors" })).toHaveTextContent(
      "2 of 8 streams could not be read · redis gone",
    );
  });

  it("says what an empty list means: all streams, one stream, and before anything loaded", () => {
    const { rerender } = render(<ActivityBench activity={activity({ rows: [] })} onWhy={() => {}} />);
    expect(screen.getByText("Nothing on any stream yet.")).toBeInTheDocument();
    expect(screen.getByText("8 streams · 0 entries")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], loaded: false })} onWhy={() => {}} />);
    expect(screen.getByText("8 streams · nothing loaded yet")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], solo: "user_requests" })} onWhy={() => {}} />);
    expect(screen.getByText("Nothing on this stream yet.")).toBeInTheDocument();
    expect(screen.getByText("UR · 0 entries · nothing has been written")).toBeInTheDocument();

    rerender(<ActivityBench activity={activity({ rows: [], solo: "user_requests", loaded: false })} onWhy={() => {}} />);
    expect(screen.getByText("UR · nothing loaded yet")).toBeInTheDocument();
  });

  it("opens a row on tap, solos from its pill, and asks why only on a reflex row", () => {
    const onWhy = vi.fn();
    const a = activity({ expanded: reflex.key });
    const { rerender } = render(<ActivityBench activity={a} onWhy={onWhy} />);
    fireEvent.click(screen.getByText("Is the back door locked?"));
    expect(a.toggle).toHaveBeenCalledWith(request.key);

    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({ stream: "reflex_observations", entry: reflex.entry });
    fireEvent.click(screen.getByRole("button", { name: "Only RX" }));
    expect(a.setSolo).toHaveBeenCalledWith("reflex_observations");

    rerender(<ActivityBench activity={activity({ expanded: request.key })} onWhy={onWhy} />);
    expect(screen.getByRole("button", { name: "Only UR" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Why · causal thread" })).toBeNull();
  });

  it("pauses and resumes the feed from one button that says how many wait", () => {
    const a = activity();
    const { rerender } = render(<ActivityBench activity={a} onWhy={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Pause feed" }));
    expect(a.pause).toHaveBeenCalledTimes(1);

    const b = activity({ paused: true, heldCount: 0 });
    rerender(<ActivityBench activity={b} onWhy={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Resume" }));
    expect(b.resume).toHaveBeenCalledTimes(1);

    rerender(<ActivityBench activity={activity({ paused: true, heldCount: 3 })} onWhy={() => {}} />);
    expect(screen.getByRole("button", { name: "Resume · 3 new" })).toBeInTheDocument();
  });

  it("keeps the Resume button's label legible on the accent in both themes", () => {
    for (const theme of ["dark", "light"] as const) {
      expect(contrast(token(theme, "on-accent"), token(theme, "accent"))).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("All streams clears the solo and is dimmed when there is none", () => {
    const a = activity({ solo: "events" });
    const { rerender } = render(<ActivityBench activity={a} onWhy={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "All streams" }));
    expect(a.setSolo).toHaveBeenCalledWith(null);

    rerender(<ActivityBench activity={activity()} onWhy={() => {}} />);
    expect(screen.getByRole("button", { name: "All streams" })).toBeDisabled();
  });

  it("holds the reader's place when a row lands on top, and starts a soloed list at the top", () => {
    const { rerender } = render(<ActivityBench activity={activity()} onWhy={() => {}} />);
    const scroll = stubScroll(screen.getByRole("list"), 1000);
    scroll.top = 400;

    // An older page appends below: the reader has not moved.
    rerender(<ActivityBench activity={activity({ rows: [reflex, request, older] })} onWhy={() => {}} />);
    expect(scroll.top).toBe(400);

    // A live row lands on top and the list grows by 120: so does the offset,
    // which leaves the same rows under the thumb.
    scroll.grow(1120);
    rerender(<ActivityBench activity={activity({ rows: [newest, reflex, request, older] })} onWhy={() => {}} />);
    expect(scroll.top).toBe(520);

    // Soloing is a different list; it starts at its own newest row.
    rerender(
      <ActivityBench
        activity={activity({ rows: [newest, reflex, request, older], solo: "user_requests" })}
        onWhy={() => {}}
      />,
    );
    expect(scroll.top).toBe(0);
  });
});
