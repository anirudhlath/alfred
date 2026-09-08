import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { TimelineItem } from "@/lib/history";
import { Timeline } from "./Timeline";

let scrollHeight = 1000;
let clientHeight = 400;
let resize: ResizeObserverCallback | null = null;

beforeEach(() => {
  scrollHeight = 1000;
  clientHeight = 400;
  // jsdom does no layout: scrollHeight/clientHeight are 0 and scrollTop is a
  // no-op. Give the three of them real behaviour so the anchoring rule can be
  // tested at all — including the clamp, or the tests could not tell anchoring
  // from an over-scroll no browser reports.
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
      this._top = Math.max(0, Math.min(value, scrollHeight - clientHeight));
    },
  });
  // The setup stub observes nothing. Keep the latest observer's callback so a
  // test can play a resize at it.
  resize = null;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: ResizeObserverCallback) {
        resize = callback;
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
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

function list(): HTMLElement {
  return screen.getByRole("log", { name: "Conversation" });
}

describe("Timeline rows", () => {
  it("is a log, named for what it holds", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);
    expect(list()).toBeInTheDocument();
  });

  it("draws a divider with its label", () => {
    render(
      <Timeline
        items={[{ kind: "divider", id: "d:1", at: you.at, label: "earlier today" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("earlier today")).toBeInTheDocument();
  });

  it("renders your bubble without a stamp when it was sent, and names you to a screen reader", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);
    const bubble = screen.getByText("What have I got tomorrow morning?");
    expect(bubble).toHaveStyle({ opacity: "1" });
    expect(within(bubble).getByText("You:")).toHaveClass("sr-only");
    expect(screen.queryByText("not sent · will retry when connected")).toBeNull();
  });

  it("fades an unsent bubble and says it will be retried", () => {
    render(<Timeline items={[{ ...you, state: "unsent" }]} firstDayGreeting={null} />);
    expect(screen.getByText("What have I got tomorrow morning?")).toHaveStyle({ opacity: "0.6" });
    expect(screen.getByText("not sent · will retry when connected")).toBeInTheDocument();
  });

  it("gives Alfred mood, tools and a time, keeps his line breaks, and names him", () => {
    render(<Timeline items={[alfred]} firstDayGreeting={null} />);
    expect(
      screen.getByText("pleased · calendar.today, weather.forecast · 20:52"),
    ).toBeInTheDocument();
    const text = screen.getByText(/The dentist at nine, sir\./);
    expect(text).toHaveClass("whitespace-pre-line");
    expect(text).toHaveStyle({ color: "var(--fg)" });
    expect(within(text).getByText("Alfred:")).toHaveClass("sr-only");
  });

  it("says no tools rather than an empty gap", () => {
    render(
      <Timeline items={[{ ...alfred, actions: [], mood: undefined }]} firstDayGreeting={null} />,
    );
    expect(screen.getByText("neutral · no tools · 20:52")).toBeInTheDocument();
  });

  it("marks an error reply as one instead of inventing a mood, and dims it", () => {
    render(
      <Timeline
        items={[{ ...alfred, text: "No reply in 60 s.", actions: [], error: true }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("error · 20:52")).toBeInTheDocument();
    expect(screen.getByText("No reply in 60 s.")).toHaveStyle({ color: "var(--fg2)" });
  });

  it.each([120, 210, 255] as const)("colours an act row by its stream hue, %i", (hue) => {
    render(
      <Timeline
        items={[
          {
            kind: "act",
            id: "rx:1",
            at: you.at,
            hue,
            text: "movie started, evening",
            meta: "17:58 · reflex · home.light_set",
          },
        ]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByText("movie started, evening")).toBeInTheDocument();
    const mark = screen.getByTestId("act-mark");
    expect(mark).toHaveAttribute("data-hue", String(hue));
    expect(mark).toHaveStyle({ background: `oklch(0.62 0.11 ${hue})` });
    // Decoration: the meta line already says which mind acted.
    expect(mark).toHaveAttribute("aria-hidden", "true");
  });

  it("strikes a tombstone through, on a faded row", () => {
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
    const title = screen.getByText("Lock unlock");
    expect(title).toHaveClass("line-through");
    expect(title.closest("div.opacity-80")).not.toBeNull();
    expect(screen.getByText("expired 07:46 · not done · asked 07:41")).toBeInTheDocument();
  });

  it("says how much audio is with the server while transcribing, as a status", () => {
    render(
      <Timeline
        items={[{ kind: "transcribing", id: "tr:1", at: you.at, seconds: 2.4 }]}
        firstDayGreeting={null}
      />,
    );
    const status = screen.getByRole("status");
    expect(within(status).getByText("Transcribing…")).toBeInTheDocument();
    expect(
      within(status).getByText("audio sent · 2.4 s · waiting on server"),
    ).toBeInTheDocument();
  });

  it("names which mind is working, as a status, behind three staggered dots", () => {
    render(
      <Timeline
        items={[{ kind: "thinking", id: "think", at: you.at, detail: "working" }]}
        firstDayGreeting={null}
      />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("conscious mind · working");
    const dots = screen.getByTestId("thinking-dots");
    expect(dots).toHaveAttribute("aria-hidden", "true");
    expect(Array.from(dots.children, (dot) => (dot as HTMLElement).style.animation)).toEqual([
      "breathe 1.2s 0s ease-in-out infinite",
      "breathe 1.2s 0.2s ease-in-out infinite",
      "breathe 1.2s 0.4s ease-in-out infinite",
    ]);
  });

  it("keys rows by id, so two of yours in a row do not collide", () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      render(
        <Timeline
          items={[you, { ...you, id: "you:2", text: "And the windows?" }]}
          firstDayGreeting={null}
        />,
      );
      expect(screen.getByText("What have I got tomorrow morning?")).toBeInTheDocument();
      expect(screen.getByText("And the windows?")).toBeInTheDocument();
      expect(errors).not.toHaveBeenCalled();
    } finally {
      errors.mockRestore();
    }
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

  it("keeps the greeting out once there is history", () => {
    render(<Timeline items={[you]} firstDayGreeting="Good evening, sir." />);
    expect(screen.getByText("What have I got tomorrow morning?")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing has happened yet/)).toBeNull();
  });

  it("shows nothing at all when the thread is empty and there is no greeting", () => {
    render(<Timeline items={[]} firstDayGreeting={null} />);
    expect(list().firstElementChild).toBeEmptyDOMElement();
    expect(screen.queryByText(/Nothing has happened yet/)).toBeNull();
  });
});

describe("Timeline anchoring", () => {
  // A 1000 px thread in a 400 px box scrolls at most 600; 1400 px scrolls 1000.
  it("sits at the bottom as rows arrive", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);
    expect(list().scrollTop).toBe(600);

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);
    expect(list().scrollTop).toBe(1000);
  });

  it("stops following once the user scrolls up to read", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);

    list().scrollTop = 200; // 1000 - 200 - 400 = 400 px from the bottom
    fireEvent.scroll(list());

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list().scrollTop).toBe(200);
  });

  it("treats a 120 px bounce as still being at the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);

    list().scrollTop = 480; // 1000 - 480 - 400 = 120, exactly the slack
    fireEvent.scroll(list());

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list().scrollTop).toBe(1000);
  });

  it("holds at 121 px, one past the slack", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);

    list().scrollTop = 479;
    fireEvent.scroll(list());

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list().scrollTop).toBe(479);
  });

  it("follows again once the user returns to the bottom", () => {
    const { rerender } = render(<Timeline items={[you]} firstDayGreeting={null} />);

    list().scrollTop = 100;
    fireEvent.scroll(list());
    list().scrollTop = 600; // back within the slack
    fireEvent.scroll(list());

    scrollHeight = 1400;
    rerender(<Timeline items={[you, alfred]} firstDayGreeting={null} />);

    expect(list().scrollTop).toBe(1000);
  });

  it("re-anchors when the box or its content resizes, while following", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);
    expect(resize).not.toBeNull();

    clientHeight = 200; // the keyboard came up
    resize?.([], {} as ResizeObserver);
    expect(list().scrollTop).toBe(800);

    scrollHeight = 1100; // the webfonts swapped in
    resize?.([], {} as ResizeObserver);
    expect(list().scrollTop).toBe(900);
  });

  it("leaves a reader alone when the box resizes", () => {
    render(<Timeline items={[you]} firstDayGreeting={null} />);

    list().scrollTop = 100;
    fireEvent.scroll(list());

    clientHeight = 200;
    resize?.([], {} as ResizeObserver);
    expect(list().scrollTop).toBe(100);
  });
});
