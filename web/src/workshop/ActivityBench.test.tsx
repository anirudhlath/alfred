import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { FeedRow } from "@/lib/feed";
import { hhmm } from "@/lib/format";
import { STREAMS, type StreamName } from "@/lib/streams";
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

describe("ActivityBench", () => {
  it("lists the rows with the older button on top when there is something older", () => {
    const a = activity({ cursor: "1788800280000-0" });
    render(<ActivityBench activity={a} onWhy={() => {}} />);
    const list = screen.getByRole("list");
    const items = within(list).getAllByRole("listitem");
    const older = within(items[0]).getByRole("button", { name: "↑ older · before cursor 1788800280000-0" });
    expect(older).not.toBeDisabled();
    fireEvent.click(older);
    expect(a.loadOlder).toHaveBeenCalledTimes(1);
    expect(within(items[1]).getByText("observed media_player.tv · acted")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("has no older button when nothing older exists", () => {
    render(<ActivityBench activity={activity()} onWhy={() => {}} />);
    expect(screen.queryByRole("button", { name: /older/ })).toBeNull();
  });

  it("says fetching, and waits, while an older page is on its way", () => {
    render(
      <ActivityBench activity={activity({ cursor: "1788800280000-0", fetchingOlder: true })} onWhy={() => {}} />,
    );
    expect(
      screen.getByRole("button", { name: "↑ older · fetching before cursor 1788800280000-0" }),
    ).toBeDisabled();
  });

  it("says the feed stopped, and when, while the socket is down", () => {
    const at = new Date(2026, 8, 7, 21, 14, 0).getTime();
    const { rerender } = render(<ActivityBench activity={activity({ live: false, liveAt: at })} onWhy={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent(`Feed stopped at ${hhmm(at)}. Nothing below is live.`);
    rerender(<ActivityBench activity={activity({ live: false, liveAt: null })} onWhy={() => {}} />);
    expect(screen.getByRole("status")).toHaveTextContent("Feed has not been live yet. Nothing below is live.");
  });

  it("shows a read failure as a status line under the banner", () => {
    render(
      <ActivityBench activity={activity({ error: "2 of 8 streams could not be read · redis gone" })} onWhy={() => {}} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("2 of 8 streams could not be read · redis gone");
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
    const pause = screen.getByRole("button", { name: "Pause feed" });
    expect(pause.style.background).toBe("var(--ink)");
    fireEvent.click(pause);
    expect(a.pause).toHaveBeenCalledTimes(1);

    const b = activity({ paused: true, heldCount: 0 });
    rerender(<ActivityBench activity={b} onWhy={() => {}} />);
    const resume = screen.getByRole("button", { name: "Resume" });
    expect(resume.style.background).toBe("var(--accent)");
    fireEvent.click(resume);
    expect(b.resume).toHaveBeenCalledTimes(1);

    rerender(<ActivityBench activity={activity({ paused: true, heldCount: 3 })} onWhy={() => {}} />);
    expect(screen.getByRole("button", { name: "Resume · 3 new" })).toBeInTheDocument();
  });

  it("All streams clears the solo and is dimmed when there is none", () => {
    const a = activity({ solo: "events" });
    const { rerender } = render(<ActivityBench activity={a} onWhy={() => {}} />);
    const all = screen.getByRole("button", { name: "All streams" });
    expect(all.style.opacity).toBe("1");
    fireEvent.click(all);
    expect(a.setSolo).toHaveBeenCalledWith(null);

    rerender(<ActivityBench activity={activity()} onWhy={() => {}} />);
    const dimmed = screen.getByRole("button", { name: "All streams" });
    expect(dimmed.style.opacity).toBe("0.4");
    expect(dimmed).toBeDisabled();
  });
});
