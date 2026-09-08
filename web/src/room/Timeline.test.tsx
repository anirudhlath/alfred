import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import type { TimelineItem } from "@/lib/history";
import { Timeline } from "./Timeline";

let scrollHeight = 1000;
let clientHeight = 400;

beforeEach(() => {
  scrollHeight = 1000;
  clientHeight = 400;
  // jsdom does no layout: scrollHeight/clientHeight are 0 and scrollTop is a
  // no-op. Give the three of them real behaviour so the anchoring rule can be
  // tested at all.
  Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
    configurable: true,
    get: () => scrollHeight,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => clientHeight,
  });
  Object.defineProperty(HTMLElement.prototype, "scrollTop", {
    configurable: true,
    get(this: HTMLElement & { _top?: number }) {
      return this._top ?? 0;
    },
    set(this: HTMLElement & { _top?: number }, value: number) {
      this._top = value;
    },
  });
});

const you: TimelineItem = {
  kind: "you",
  id: "you:1",
  at: "2026-09-07T20:52:03",
  text: "What have I got tomorrow morning?",
  state: "sent",
};

const alfred: TimelineItem = {
  kind: "alfred",
  id: "alfred:1",
  at: "2026-09-07T20:52:06",
  text: "The dentist at nine, sir.\nI'd leave by twenty to.",
  mood: "pleased",
  actions: ["calendar.today", "weather.forecast"],
};

describe("Timeline rows", () => {
  it("draws a divider with its label", () => {
    render(
      <Timeline
        items={[{ kind: "divider", id: "d:1", at: you.at, label: "earlier today" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("earlier today")).toBeInTheDocument();
  });

  it("renders your bubble without a stamp when it was sent", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);
    expect(screen.getByText("What have I got tomorrow morning?")).toBeInTheDocument();
    expect(screen.queryByText("not sent · will retry when connected")).toBeNull();
  });

  it("says an unsent bubble will be retried", () => {
    render(<Timeline items={[{ ...you, state: "unsent" }]} firstDayGreeting={null} />);
    expect(screen.getByText("not sent · will retry when connected")).toBeInTheDocument();
  });

  it("gives Alfred mood, tools and a time, and keeps his line breaks", () => {
    render(<Timeline items={[alfred]} firstDayGreeting={null} />);
    expect(
      screen.getByText("pleased · calendar.today, weather.forecast · 20:52"),
    ).toBeInTheDocument();
    const text = screen.getByText(/The dentist at nine, sir\./);
    expect(text).toHaveClass("whitespace-pre-line");
  });

  it("says no tools rather than an empty gap", () => {
    render(
      <Timeline items={[{ ...alfred, actions: [], mood: undefined }]} firstDayGreeting={null} />,
    );
    expect(screen.getByText("neutral · no tools · 20:52")).toBeInTheDocument();
  });

  it("marks an error reply as one instead of inventing a mood", () => {
    render(
      <Timeline
        items={[{ ...alfred, text: "No reply in 60 s.", actions: [], error: true }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("error · 20:52")).toBeInTheDocument();
  });

  it("colours an act row by its stream hue", () => {
    render(
      <Timeline
        items={[
          {
            kind: "act",
            id: "rx:1",
            at: you.at,
            hue: 210,
            text: "movie started, evening",
            meta: "17:58 · reflex · home.light_set",
          },
        ]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("movie started, evening")).toBeInTheDocument();
    expect(screen.getByTestId("act-mark")).toHaveAttribute("data-hue", "210");
  });

  it("strikes a tombstone through", () => {
    render(
      <Timeline
        items={[
          {
            kind: "tombstone",
            id: "tomb:a91f",
            at: you.at,
            title: "Lock unlock",
            meta: "expired 07:46 · not done · asked 07:41",
          },
        ]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("Lock unlock")).toHaveClass("line-through");
    expect(screen.getByText("expired 07:46 · not done · asked 07:41")).toBeInTheDocument();
  });

  it("says how much audio is with the server while transcribing", () => {
    render(
      <Timeline
        items={[{ kind: "transcribing", id: "tr:1", at: you.at, seconds: 2.4 }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("Transcribing…")).toBeInTheDocument();
    expect(screen.getByText("audio sent · 2.4 s · waiting on server")).toBeInTheDocument();
  });

  it("names which mind is working", () => {
    render(
      <Timeline
        items={[{ kind: "thinking", id: "think", at: you.at, detail: "working" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("conscious mind · working")).toBeInTheDocument();
  });

  it("greets an empty house and says why it is empty", () => {
    render(<Timeline items={[]} firstDayGreeting="Good evening, sir." />);
    expect(
      screen.getByText(
        "Good evening, sir. Nothing has happened yet; I'm watching the house and listening for you.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("first run · no memories · 0 routines · hold the button to speak"),
    ).toBeInTheDocument();
  });

  it("shows nothing at all when there is history but no first-day greeting", () => {
    render(<Timeline items={[]} firstDayGreeting={null} />);
    expect(screen.queryByText(/Nothing has happened yet/)).toBeNull();
  });
});

describe("Timeline anchoring", () => {
  it("sits at the bottom as rows arrive", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");
    expect(list.scrollTop).toBe(1000);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);
    expect(list.scrollTop).toBe(1400);
  });

  it("stops following once the user scrolls up to read", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 200; // 1000 - 200 - 400 = 400 px from the bottom
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(200);
  });

  it("treats a 120 px bounce as still being at the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 480; // 1000 - 480 - 400 = 120, exactly the slack
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(1400);
  });

  it("follows again once the user returns to the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    const list = screen.getByTestId("timeline");

    list.scrollTop = 100;
    fireEvent.scroll(list);
    list.scrollTop = 600; // back within the slack
    fireEvent.scroll(list);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list.scrollTop).toBe(1400);
  });
});
