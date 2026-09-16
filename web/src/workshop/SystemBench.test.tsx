import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DND_UNCONFIRMED, healthGrid, spendNote, type Health } from "@/lib/system";
import { overviewFixture, SYSTEM_NOW } from "@/test/fixtures";
import { SystemBench } from "./SystemBench";
import type { Maintenance, Quiet, System } from "./useSystem";

/**
 * The health grid as `lib/system.ts` really derives it, rather than four cells
 * written out by hand here: the bench's promise is that it prints what the
 * derivation said, and a fixture invented in this file could agree with the
 * bench while both disagreed with the house.
 */
const LIVE_HEALTH = healthGrid({
  overview: overviewFixture,
  registryRead: true,
  home: {
    data: { name: "home-service", healthy: true, latency_ms: 210 },
    isPending: false,
    isError: false,
    status: null,
  },
});

/** The same grid before anything has been read: no overview, no registry. */
const UNREAD_HEALTH = healthGrid({ overview: undefined, registryRead: false, home: undefined });

/**
 * Local instants, never `Z`-suffixed: CI runs at UTC and a developer does not,
 * so an ISO stamp with a zone in it would print one `hh:mm` here and another
 * there. 03:00 this morning, 03:00 tomorrow, 08:30 tomorrow, 22:00 tonight.
 */
const LAST_RUN = new Date(2026, 8, 16, 3, 0, 0).toISOString();
const NEXT_RUN = new Date(2026, 8, 17, 3, 0, 0).toISOString();
const UNTIL_MORNING = new Date(2026, 8, 17, 8, 30, 0).toISOString();
const UNTIL_TONIGHT = new Date(2026, 8, 16, 22, 0, 0).toISOString();

/** What each expiry chip works out to, read against `SYSTEM_NOW` (21:30). */
const IN_AN_HOUR = new Date(2026, 8, 16, 22, 30, 0).toISOString();
const NEXT_NOON = new Date(2026, 8, 17, 12, 0, 0).toISOString();

const FOOTNOTE = "A meeting in your calendar can also quiet Alfred; that is not shown here.";
const DRAIN_NOTE = "the notifier sends them when it next reads the queue";
const RUN_NOTE = "queued only; the run reports on the events stream, not here";
const RAN_NOTE = "progress shows on the events stream as consolidation.*";

function quiet(overrides: Partial<Quiet> = {}): Quiet {
  return {
    active: false,
    until: null,
    held: 2,
    setting: false,
    error: null,
    set: vi.fn(),
    onHeld: vi.fn(),
    ...overrides,
  };
}

function maintenance(overrides: Partial<Maintenance> = {}): Maintenance {
  return {
    last: LAST_RUN,
    reviewed: 42,
    next: NEXT_RUN,
    idleMinutes: 30,
    drainedAt: null,
    ranAt: null,
    error: null,
    drain: vi.fn(),
    run: vi.fn(),
    ...overrides,
  };
}

/**
 * The bench is a pure view over one state object, so its tests build that
 * object rather than a `QueryClient` — the same bargain `TriggersBench.test.tsx`
 * and `MemoryBench.test.tsx` make with theirs. The five sections task 9 adds are
 * given empty, honest sub-objects: nothing in this file reads them.
 */
function state(overrides: Partial<System> = {}): System {
  return {
    overview: overviewFixture,
    health: LIVE_HEALTH,
    quiet: quiet(),
    sessions: { list: [], ended: {}, ending: {}, end: vi.fn(), error: null },
    credentials: { list: [], error: null },
    integrations: { list: [], rows: {}, saves: {}, save: vi.fn(), error: null },
    attention: { domains: [], saving: {}, allow: vi.fn(), ask: vi.fn(), error: null },
    pairing: { code: null, expiresAt: null, minting: false, error: null, mint: vi.fn() },
    maintenance: maintenance(),
    loading: false,
    error: null,
    ...overrides,
  };
}

/** A health grid whose words and whose flags deliberately disagree. */
function lying(cells: Partial<Health>): Health {
  return { ...LIVE_HEALTH, ...cells };
}

const dots = () => screen.getAllByTestId("health-dot");
const values = () => screen.getAllByTestId("health-value");
const stamp = () => screen.getByTestId("health-stamp");
const dnd = () => screen.getByRole("switch", { name: "Do-not-disturb" });
const chips = () =>
  within(screen.getByRole("group", { name: "Quiet until" })).getAllByRole("button");

/**
 * 44 px of touch target, however it is reached: a row's own height, or 32 px of
 * switch with the 6 px `after` box above and below it that `TriggerRow` sizes
 * its switch with.
 */
function tall(element: HTMLElement): boolean {
  const classes = element.className;
  return (
    /(^|\s)(h-11|min-h-11|min-h-14)(\s|$)/.test(classes) ||
    (classes.includes("h-8") && classes.includes("after:-inset-y-1.5"))
  );
}

/**
 * 21:30 on the evening the System fixtures were written. Pinned, because the
 * bench reads the clock when it mounts and dates every stamp against it: on the
 * real clock `last 03:00 earlier today` becomes `03:00 16 Sep` tomorrow.
 */
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(SYSTEM_NOW);
});
afterEach(() => vi.useRealTimers());

describe("SystemBench · Health", () => {
  it("names the four things it can measure, and what each one is", () => {
    render(<SystemBench system={state()} />);
    expect(values().map((cell) => cell.textContent)).toEqual([
      "alive",
      "380 ms",
      "2.1 ev/s",
      "ok",
    ]);
    expect(screen.getByText("bus · redis · 3 streams")).toBeInTheDocument();
    expect(screen.getByText("reflex · reflex-3b")).toBeInTheDocument();
    expect(screen.getByText("event rate · 5-minute mean")).toBeInTheDocument();
    expect(screen.getByText("home assistant · 210 ms")).toBeInTheDocument();
  });

  // The flag, never the word: a grid that dimmed because the string it was
  // handed happened not to read `alive` is one rename away from lying.
  it("fills each dot from the cell's own flag rather than from the word in it", () => {
    const health = lying({
      bus: { value: "alive", note: "bus · redis · 3 streams", alive: false },
      rate: { value: "2.1 ev/s", note: "event rate · 5-minute mean", alive: true },
    });
    render(<SystemBench system={state({ health })} />);
    expect(dots()[0]).toHaveStyle({ background: "var(--muted)" });
    expect(dots()[2]).toHaveStyle({ background: "var(--green)" });
  });

  it("keeps an unknown reading off the colour that means trouble", () => {
    render(<SystemBench system={state({ health: UNREAD_HEALTH })} />);
    for (const dot of dots()) {
      expect(dot).toHaveStyle({ background: "var(--muted)" });
      expect(dot).not.toHaveStyle({ background: "var(--accent)" });
    }
  });

  // §5.2: a stale number at full strength is the failure this dims away from.
  // By token and not by opacity — a whole-grid alpha composites every layer
  // under it and is invisible to `src/test/contrast.ts`.
  it("reads nothing back before the house has answered, and recedes to say so", () => {
    render(<SystemBench system={state({ health: UNREAD_HEALTH, overview: undefined })} />);
    expect(values().map((cell) => cell.textContent)).toEqual([
      "unknown",
      "—",
      "— ev/s",
      "—",
    ]);
    expect(screen.getByText("bus · redis · not read yet")).toBeInTheDocument();
    for (const cell of values()) expect(cell).toHaveStyle({ color: "var(--fg2)" });
  });

  // react-query keeps the last answer, so `alive` outlives the evidence for it.
  // A green light over a number nothing is refreshing is the §5.2 failure.
  it("puts out a dot that is only still alive because the last read is old", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(dots()[0]).toHaveStyle({ background: "var(--green)" });
    rerender(<SystemBench system={state({ error: "500 · overview" })} />);
    for (const dot of dots()) expect(dot).toHaveStyle({ background: "var(--muted)" });
    for (const cell of values()) expect(cell).toHaveStyle({ color: "var(--fg2)" });
  });

  it("stamps the moment it last knew, to the second, while the reads land", () => {
    render(<SystemBench system={state()} />);
    expect(stamp()).toHaveTextContent("live · 21:30:00");
    expect(stamp()).toHaveClass("t-meta-strong");
  });

  it("says how long the house has been unknown once a read fails", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    rerender(<SystemBench system={state({ error: "500 · overview" })} />);
    expect(stamp()).toHaveTextContent("unknown since 21:30");
    expect(stamp()).toHaveStyle({ color: "var(--accent-text)" });
  });

  it("invents no moment for a bench that was never live", () => {
    render(<SystemBench system={state({ overview: undefined, health: UNREAD_HEALTH })} />);
    expect(stamp()).toHaveTextContent("unknown since --:--");
  });

  it("keeps the read error where a reader can find it", () => {
    render(<SystemBench system={state({ error: "500 · overview" })} />);
    const region = screen.getByRole("status", { name: "Read errors" });
    expect(region).toHaveTextContent("500 · overview");
  });
});

describe("SystemBench · Cloud spend", () => {
  it("states the spend against the cap, and draws it", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText("Cloud spend today")).toBeInTheDocument();
    const note = "$1.42 of $5.00 today · 38 requests · $0.037 each";
    expect(spendNote(overviewFixture.cost)).toBe(note);
    expect(screen.getByText(note)).toBeInTheDocument();
    // Labelled by the sentence beside it: a bar with no text equivalent is a
    // fact only the sighted reader gets.
    expect(screen.getByRole("img", { name: note })).toBeInTheDocument();
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ width: "28.4%" });
  });

  it("clamps a bar that has run past its cap", () => {
    const cost = { date: "2026-09-16", spend_usd: 9, cap_usd: 5 };
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost } })} />);
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ width: "100.0%" });
  });

  it("draws an empty bar and says why when there is no cap to measure against", () => {
    const cost = { date: "2026-09-16", spend_usd: 1.42, cap_usd: 0, request_count: 38 };
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost } })} />);
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ width: "0.0%" });
    expect(screen.getByText("$1.42 today · no cap set · 38 requests")).toBeInTheDocument();
    expect(screen.getByTestId("spend-card").textContent).not.toContain("NaN");
  });

  it("says nothing at all about a day the server reported no spend for", () => {
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost: null } })} />);
    expect(screen.getByText("no spend recorded today")).toBeInTheDocument();
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ width: "0.0%" });
  });
});

describe("SystemBench · Quiet", () => {
  it("reports the stored position and asks the house for the other one", () => {
    const system = state();
    render(<SystemBench system={system} />);
    expect(dnd()).not.toBeChecked();
    fireEvent.click(dnd());
    expect(system.quiet.set).toHaveBeenCalledWith(true, null);
  });

  // `aria-disabled`, never `disabled`: a disabled control cannot take focus, so
  // the note describing it is never announced and a reader hears nothing at all
  // for the whole window.
  it("waits on the house without taking focus away from the control", () => {
    const system = state({ quiet: quiet({ setting: true }) });
    render(<SystemBench system={system} />);
    expect(dnd()).toHaveAttribute("aria-busy", "true");
    expect(dnd()).toHaveAttribute("aria-disabled", "true");
    expect(dnd()).not.toBeDisabled();
    fireEvent.click(dnd());
    expect(system.quiet.set).not.toHaveBeenCalled();
  });

  // The direct write (decision 6's one exception): the switch moves, but only
  // once the overview re-read has confirmed the new position.
  it("moves when the house confirms, and says that it applied", () => {
    const set = vi.fn();
    const { rerender } = render(<SystemBench system={state({ quiet: quiet({ set }) })} />);
    fireEvent.click(dnd());
    expect(screen.queryByText("applied")).not.toBeInTheDocument();
    rerender(<SystemBench system={state({ quiet: quiet({ active: true, set }) })} />);
    expect(dnd()).toBeChecked();
    expect(screen.getByText("applied")).toBeInTheDocument();
  });

  it("never claims applied over a write the house did not confirm", () => {
    const set = vi.fn();
    const { rerender } = render(<SystemBench system={state({ quiet: quiet({ set }) })} />);
    fireEvent.click(dnd());
    rerender(<SystemBench system={state({ quiet: quiet({ set, error: DND_UNCONFIRMED }) })} />);
    expect(screen.getByText(DND_UNCONFIRMED)).toBeInTheDocument();
    expect(screen.queryByText(/applied/)).not.toBeInTheDocument();
  });

  it("says what quiet means, in each of the three states it has", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(screen.getByText("off · urgent still speaks regardless")).toBeInTheDocument();

    rerender(
      <SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_MORNING }) })} />,
    );
    expect(screen.getByText("on · until 08:30 · queue drains then")).toBeInTheDocument();

    rerender(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(
      screen.getByText("on · no expiry · queue will not drain on its own"),
    ).toBeInTheDocument();
  });

  it("offers an expiry only while the quiet is on", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(screen.queryByRole("group", { name: "Quiet until" })).not.toBeInTheDocument();
    rerender(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(chips().map((chip) => chip.textContent)).toEqual([
      "1 h",
      "until noon",
      "until 22:00",
      "no expiry",
    ]);
  });

  it("sends the instant each chip names, and null for the one that names none", () => {
    const system = state({ quiet: quiet({ active: true }) });
    render(<SystemBench system={system} />);
    for (const chip of chips()) fireEvent.click(chip);
    expect(system.quiet.set).toHaveBeenNthCalledWith(1, true, IN_AN_HOUR);
    expect(system.quiet.set).toHaveBeenNthCalledWith(2, true, NEXT_NOON);
    expect(system.quiet.set).toHaveBeenNthCalledWith(3, true, UNTIL_TONIGHT);
    expect(system.quiet.set).toHaveBeenNthCalledWith(4, true, null);
  });

  it("marks the expiry the house is holding", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_TONIGHT }) })} />);
    expect(chips().map((chip) => chip.getAttribute("aria-pressed"))).toEqual([
      "false",
      "false",
      "true",
      "false",
    ]);
  });

  it("marks no expiry as the one that is set when the house holds none", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(chips()[3]).toHaveAttribute("aria-pressed", "true");
  });

  // A queue with a drain and a queue without one are different facts.
  it("counts what is held back, and says when nothing will drain it", () => {
    const { rerender } = render(
      <SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_MORNING }) })} />,
    );
    expect(screen.getByText("2 held")).toBeInTheDocument();
    rerender(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(screen.getByText("2 · growing")).toHaveStyle({ color: "var(--accent-text)" });
  });

  it("opens the held-back sheet through the Room's own callback", () => {
    const system = state();
    render(<SystemBench system={system} />);
    fireEvent.click(screen.getByRole("button", { name: /Held back/ }));
    expect(system.quiet.onHeld).toHaveBeenCalledTimes(1);
  });

  it("queues a drain and never claims anything was sent", () => {
    const system = state();
    const { rerender } = render(<SystemBench system={system} />);
    fireEvent.click(screen.getByRole("button", { name: "Send them now" }));
    expect(system.maintenance.drain).toHaveBeenCalledTimes(1);

    const drained = state({ maintenance: maintenance({ drainedAt: SYSTEM_NOW }) });
    rerender(<SystemBench system={drained} />);
    const note = screen.getByText(`queued 21:30 · ${DRAIN_NOTE}`);
    expect(note).toHaveStyle({ color: "var(--accent-text)" });
    expect(screen.queryByText(/sent\b/i)).not.toBeInTheDocument();
  });

  it("owns up to the quiet it cannot see", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText(FOOTNOTE)).toHaveClass("t-meta-strong");
  });
});

describe("SystemBench · Maintenance", () => {
  it("dates the last consolidation and names the next", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText("last 03:00 earlier today · 42 reviewed")).toBeInTheDocument();
    expect(screen.getByText("next 03:00 tomorrow")).toBeInTheDocument();
  });

  it("says never run rather than inventing a pass", () => {
    render(
      <SystemBench
        system={state({ maintenance: maintenance({ last: null, reviewed: null, next: null }) })}
      />,
    );
    expect(screen.getByText("never run")).toBeInTheDocument();
    expect(screen.queryByText(/^next /)).not.toBeInTheDocument();
  });

  it("queues a run and says where the run itself will report", () => {
    const system = state();
    const { rerender } = render(<SystemBench system={system} />);
    const button = screen.getByRole("button", { name: "Run consolidation now" });
    expect(screen.getByText(RUN_NOTE)).toBeInTheDocument();
    fireEvent.click(button);
    expect(system.maintenance.run).toHaveBeenCalledTimes(1);

    rerender(<SystemBench system={state({ maintenance: maintenance({ ranAt: SYSTEM_NOW }) })} />);
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    expect(screen.getByText(`queued 21:30 · ${RAN_NOTE}`)).toBeInTheDocument();
    expect(screen.queryByText(/\bdone\b/i)).not.toBeInTheDocument();
  });

  it("prints the timeout the server sent", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText("Session idle timeout")).toBeInTheDocument();
    expect(screen.getByText("30 minutes")).toBeInTheDocument();
  });

  it("prints no number at all until the server has sent one", () => {
    render(<SystemBench system={state({ maintenance: maintenance({ idleMinutes: null }) })} />);
    expect(screen.getByText("Session idle timeout")).toBeInTheDocument();
    expect(screen.queryByText(/minutes/)).not.toBeInTheDocument();
  });
});

describe("SystemBench", () => {
  it("names its sections in the order the handoff puts them", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getAllByRole("heading").map((heading) => heading.textContent)).toEqual([
      "Health",
      "Quiet",
      "Maintenance",
    ]);
  });

  it("keeps every control at a thumb's height", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    for (const control of [...screen.getAllByRole("button"), ...screen.getAllByRole("switch")]) {
      expect(tall(control)).toBe(true);
    }
  });
});
