import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { STREAMS, type StreamName } from "@/lib/streams";
import { contrast, token } from "@/test/contrast";
import { reflexObservationsPage, userRequestsPage, userResponsesPage } from "@/test/fixtures";
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
/** A reply that names the tools it ran — the conversation turn's own `why?`. */
const reply: FeedRow = {
  stream: "user_responses",
  entry: userResponsesPage.entries[0],
  key: `user_responses:${userResponsesPage.entries[0].id}`,
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

/** Every stream read, except the one named — whose head read failed. */
function streamLoaded(failed?: StreamName): Record<StreamName, boolean> {
  return Object.fromEntries(STREAMS.map((name) => [name, name !== failed])) as Record<StreamName, boolean>;
}

function activity(overrides: Partial<Activity> = {}): Activity {
  return {
    rows: [reflex, request],
    counts: Object.fromEntries(STREAMS.map((name) => [name, 0])) as Record<StreamName, number>,
    streamLoaded: streamLoaded(),
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

  it("does not vouch for a stream it could not read", () => {
    // `loaded` is the *global* flag and goes true once the head read settles,
    // however it settled — all eight can have rejected. Solo one of them and
    // the note used to read `HS · 0 entries · nothing has been written`, which
    // is a claim about the server made from no evidence (spec §5.2).
    render(
      <ActivityBench
        activity={activity({ rows: [], solo: "home_state", streamLoaded: streamLoaded("home_state") })}
        onWhy={() => {}}
      />,
    );
    expect(screen.getByText("HS · could not be read")).toBeInTheDocument();
    expect(screen.queryByText(/nothing has been written/)).toBeNull();
  });

  it("still says nothing loaded yet when a stream's read never settled", () => {
    // Pause before the first head read retires it: nothing failed, nothing
    // landed, and the bench must not call that a read error either.
    render(
      <ActivityBench
        activity={activity({
          rows: [],
          solo: "home_state",
          loaded: false,
          streamLoaded: streamLoaded("home_state"),
        })}
        onWhy={() => {}}
      />,
    );
    expect(screen.getByText("HS · nothing loaded yet")).toBeInTheDocument();
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

  it("asks why on a reply too — spec §5.1's conversation turn", () => {
    // §5.1 defines Causality as correlating one *conversation turn* with the
    // activity it caused, and a reply's `actions_taken` names the actions it
    // ran: `trace.ts` joins that to `actions.tool_name`, so the column is a
    // real one and not the dashed-only noise the plan's decision 4 refused.
    const onWhy = vi.fn();
    render(
      <ActivityBench activity={activity({ rows: [reply], expanded: reply.key })} onWhy={onWhy} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Why · causal thread" }));
    expect(onWhy).toHaveBeenCalledWith({ stream: "user_responses", entry: reply.entry });
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

  it("re-measures when a row opens, so the next live row is worth only its own height", () => {
    const { rerender } = render(<ActivityBench activity={activity()} onWhy={() => {}} />);
    const scroll = stubScroll(screen.getByRole("list"), 1000);
    scroll.top = 400;

    // jsdom lays nothing out, so the height at mount was 0 and the stub landed
    // after it. One deps-changing render with nothing moving seeds the
    // measurement — the `↑ older` append does it here — and without this step
    // the rest of the test would pass off the wrong baseline.
    rerender(<ActivityBench activity={activity({ rows: [reflex, request, older] })} onWhy={() => {}} />);
    expect(scroll.top).toBe(400);

    // Opening a row unfolds 300 px of JSON. The reader did that themselves;
    // nothing moves.
    scroll.grow(1300);
    rerender(
      <ActivityBench activity={activity({ rows: [reflex, request, older], expanded: reflex.key })} onWhy={() => {}} />,
    );
    expect(scroll.top).toBe(400);

    // The live row that follows is worth its own 120 px and not the panel's
    // 300: hand back 420 and the open panel leaves the screen.
    scroll.grow(1420);
    rerender(
      <ActivityBench
        activity={activity({ rows: [newest, reflex, request, older], expanded: reflex.key })}
        onWhy={() => {}}
      />,
    );
    expect(scroll.top).toBe(520);
  });
});
