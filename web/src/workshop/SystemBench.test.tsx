import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DND_UNCONFIRMED, healthGrid, spendHeadline, spendNote, type Health } from "@/lib/system";
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
/** An expiry the house is still holding after the moment it names. */
const LAPSED = new Date(2026, 8, 16, 8, 30, 0).toISOString();
const YESTERDAY_RUN = new Date(2026, 8, 15, 3, 0, 0).toISOString();

/** What each expiry chip works out to, read against `SYSTEM_NOW` (21:30). */
const IN_AN_HOUR = new Date(2026, 8, 16, 22, 30, 0).toISOString();
const NEXT_NOON = new Date(2026, 8, 17, 12, 0, 0).toISOString();

/** When the last overview landed, sixteen minutes before the bench's clock. */
const LAST_READ = SYSTEM_NOW - 16 * 60_000;

const FOOTNOTE = "A meeting in your calendar can also quiet Alfred; that is not shown here.";
const DRAIN_TAIL = "the notifier sends them when it next reads the queue";
const DRAIN_NOTE = `queued only; ${DRAIN_TAIL}`;
const RUN_NOTE = "queued only; the run reports on the events stream, not here";
const RAN_TAIL = "progress shows on the events stream as consolidation.*";
const SPEND_AMOUNT = "$1.42 of $5.00";
const SPEND_NOTE = "38 requests · $0.037 each · at the cap, the conscious mind declines and says so";

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
    drainError: null,
    runError: null,
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
    readAt: LAST_READ,
    online: true,
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
const notes = () =>
  values().map((cell) => cell.nextElementSibling?.textContent ?? "");
const stamp = () => screen.getByTestId("health-stamp");
const dnd = () => screen.getByRole("switch", { name: "Do-not-disturb" });
const chips = () =>
  within(screen.getByRole("group", { name: "Quiet until" })).getAllByRole("button");
const pressed = () => chips().map((chip) => chip.getAttribute("aria-pressed"));
const drainNote = () => screen.getByRole("button", { name: "Send them now" }).nextElementSibling;
const described = (element: HTMLElement): (string | undefined)[] =>
  (element.getAttribute("aria-describedby") ?? "")
    .split(" ")
    .filter(Boolean)
    .map((id) => document.getElementById(id)?.textContent ?? undefined);

/**
 * 44 px of touch target, however it is reached: a row's own height, or 32 px of
 * switch with the 6 px `after` box above and below it that `Switch` sizes
 * itself with.
 */
function tall(element: HTMLElement): boolean {
  const classes = element.className.split(/\s+/);
  return (
    classes.some((name) => ["h-11", "min-h-11", "min-h-14"].includes(name)) ||
    (classes.includes("h-8") && classes.includes("after:-inset-y-1.5"))
  );
}

/**
 * One whole class, not a substring: `toContain("grid-cols-2")` also passes on
 * `grid-cols-20`, which is a promise about the layout a typo could keep.
 */
const hasClass = (element: Element | null | undefined, name: string): boolean =>
  (element?.className ?? "").split(/\s+/).includes(name);

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
    expect(notes()).toEqual([
      "bus · redis · 3 streams",
      "reflex · reflex-3b",
      "event rate · 5-min mean",
      "home assistant · 210 ms",
    ]);
    for (const cell of values()) {
      expect(cell).toHaveStyle({ color: "var(--fg)" });
      // The figure is the thing on this grid a reader compares against the one
      // they saw a minute ago, so it is the bench's one tabular number.
      expect(hasClass(cell, "t-title")).toBe(true);
      expect(hasClass(cell, "font-mono")).toBe(true);
    }
    expect(
      hasClass(
        screen.getByRole("heading", { name: "Health" }).parentElement?.nextElementSibling
          ?.firstElementChild,
        "grid-cols-2",
      ),
    ).toBe(true);
  });

  // The flag, never the word: a grid that dimmed because the string it was
  // handed happened not to read `alive` is one rename away from lying.
  it("fills each dot from the cell's own flag rather than from the word in it", () => {
    const health = lying({
      bus: { value: "alive", note: "bus · redis · 3 streams", alive: false },
      rate: { value: "2.1 ev/s", note: "event rate · 5-min mean", alive: true },
    });
    render(<SystemBench system={state({ health })} />);
    expect(dots()[0].style.background).toBe("transparent");
    expect(dots()[0].style.borderColor).toBe("var(--muted)");
    expect(dots()[2].style.background).toBe("var(--green-text)");
    expect(dots()[2].style.borderColor).toBe("var(--green-text)");
  });

  // Four cells in two rows, and the hairlines that make them read as a grid
  // rather than as four floating figures: a top edge on the second row only, a
  // left edge on the right-hand column only, so no line is ever drawn twice and
  // none is drawn against the card's own border.
  it("rules the grid between the cells and never around it", () => {
    render(<SystemBench system={state()} />);
    const cells = values().map((value) => value.parentElement);
    expect(cells.map((cell) => hasClass(cell, "border-t"))).toEqual([
      false,
      false,
      true,
      true,
    ]);
    expect(cells.map((cell) => hasClass(cell, "border-l"))).toEqual([
      false,
      true,
      false,
      true,
    ]);
    for (const cell of cells.slice(1)) expect(hasClass(cell, "border-line")).toBe(true);
  });

  // Fill versus outline, not hue: `--green` on `--muted` is 1.51:1 in light,
  // so a reader who cannot separate the two hues would have nothing at all.
  it("separates a live dot from a dead one without relying on its colour", () => {
    render(<SystemBench system={state({ health: UNREAD_HEALTH })} />);
    for (const dot of dots()) {
      expect(dot.style.background).toBe("transparent");
      expect(dot.style.borderColor).toBe("var(--muted)");
      expect(hasClass(dot, "border")).toBe(true);
      // Decorative: the value beside it says `alive` or `unknown` in words.
      expect(dot).toHaveAttribute("aria-hidden", "true");
    }
  });

  // §5.2: a stale number at full strength is the failure this dims away from.
  // By token and not by opacity — a whole-grid alpha composites every layer
  // under it and is invisible to `src/test/contrast.ts`.
  it("reads nothing back before the house has answered, and recedes to say so", () => {
    render(
      <SystemBench
        system={state({
          health: UNREAD_HEALTH,
          overview: undefined,
          readAt: null,
          online: false,
        })}
      />,
    );
    expect(values().map((cell) => cell.textContent)).toEqual(["unknown", "—", "— ev/s", "—"]);
    expect(notes()[0]).toBe("bus · redis · not read yet");
    for (const cell of values()) expect(cell).toHaveStyle({ color: "var(--fg2)" });
  });

  // The went-stale case, which is not the never-read case. react-query keeps
  // the last answer, so a dimmed `210 ms` is still a latency claim about a
  // service that may be down — `Alfred.dc.html`'s offline grid blanks all four.
  it("blanks the grid once the reads stop landing, and dates the silence", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(values().map((cell) => cell.textContent)).toEqual([
      "alive",
      "380 ms",
      "2.1 ev/s",
      "ok",
    ]);

    rerender(<SystemBench system={state({ online: false })} />);
    expect(values().map((cell) => cell.textContent)).toEqual(["?", "?", "?", "—"]);
    expect(notes()).toEqual([
      "bus · unknown since 21:14",
      "reflex · unknown",
      "event rate · unknown",
      "home assistant · unknown",
    ]);
    for (const cell of values()) expect(cell).toHaveStyle({ color: "var(--fg2)" });
    for (const dot of dots()) expect(dot.style.background).toBe("transparent");
  });

  // The whole point of `online`: with no network the poll is *paused*, so the
  // data is retained and the error stays null. A grid driven off `error` would
  // tick `live · 21:31:07` over four numbers nothing is refreshing.
  it("goes unknown on a silence that raised no error at all", () => {
    render(<SystemBench system={state({ online: false, error: null })} />);
    expect(values().map((cell) => cell.textContent)).toEqual(["?", "?", "?", "—"]);
    expect(stamp().textContent).toBe("unknown since 21:14");
  });

  it("stamps the moment it last knew, to the second, while the reads land", () => {
    render(<SystemBench system={state()} />);
    expect(stamp().textContent).toBe("live · 21:30:00");
    expect(hasClass(stamp(), "t-meta-strong")).toBe(true);
  });

  // The seconds are the point: they are what distinguishes a live panel from a
  // photograph of one.
  it("keeps the seconds moving while it is live", () => {
    render(<SystemBench system={state()} />);
    act(() => void vi.advanceTimersByTime(5_000));
    expect(stamp().textContent).toBe("live · 21:30:05");
  });

  it("stops its clock when the bench goes", () => {
    const { unmount } = render(<SystemBench system={state()} />);
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  // The stamp freezes at the read, not at its own last tick: that is the
  // instant the grid stopped being evidence, and the bus card prints it too.
  it("says how long the house has been unknown, dated to the last answer", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    act(() => void vi.advanceTimersByTime(30_000));
    rerender(<SystemBench system={state({ online: false })} />);
    expect(stamp().textContent).toBe("unknown since 21:14");
    expect(stamp()).toHaveStyle({ color: "var(--accent-text)" });
  });

  it("invents no moment for a bench that was never live", () => {
    render(
      <SystemBench
        system={state({ overview: undefined, health: UNREAD_HEALTH, readAt: null, online: false })}
      />,
    );
    expect(stamp().textContent).toBe("unknown since --:--");
  });

  it("keeps the read error where a reader can find it", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    const region = screen.getByRole("status", { name: "Read errors" });
    // Mounted whether or not it has anything to say — VoiceOver can miss a
    // region inserted with its text already in it — and out of the way while
    // it has not.
    expect(hasClass(region, "sr-only")).toBe(true);
    rerender(<SystemBench system={state({ error: "500 · overview" })} />);
    expect(region).toHaveTextContent("500 · overview");
    expect(hasClass(region, "sr-only")).toBe(false);
  });
});

describe("SystemBench · Cloud spend", () => {
  it("states the spend against the cap, and says what the cap does", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText("Cloud spend today")).toBeInTheDocument();
    expect(spendHeadline(overviewFixture.cost)).toBe(SPEND_AMOUNT);
    expect(spendNote(overviewFixture.cost)).toBe(SPEND_NOTE);
    expect(screen.getByText(SPEND_AMOUNT)).toBeInTheDocument();
    expect(screen.getByText(SPEND_NOTE)).toBeInTheDocument();
    // Labelled by both lines: a bar with no text equivalent is a fact only the
    // sighted reader gets, and the money alone does not say what the cap does.
    expect(
      screen.getByRole("img", { name: `${SPEND_AMOUNT} ${SPEND_NOTE}` }),
    ).toBeInTheDocument();
  });

  it("draws the fraction of the cap that is gone, to one decimal", () => {
    render(<SystemBench system={state()} />);
    // The string in the DOM, not `toHaveStyle`, which cannot tell
    // `28.400000000000002%` from `28.4%` — and that is the whole job of the
    // `toFixed(1)` behind it.
    expect(screen.getByTestId<HTMLElement>("spend-fill").style.width).toBe("28.4%");
  });

  it("colours the bar so it reads as a graphic and not only as a sentence", () => {
    render(<SystemBench system={state()} />);
    // `--accent` is 1.99:1 on this track in light; `--accent-text` is 4.47:1.
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ background: "var(--accent-text)" });
    // The track is 1.09:1 on the card, so it carries its own edge — an outline
    // rather than an inset shadow, which the fill would paint over.
    expect(screen.getByTestId("spend-track")).toHaveStyle({
      background: "var(--line)",
      outline: "1px solid var(--muted)",
    });
  });

  // §5.2 again, and the handoff dims this card too: the most assertive element
  // in the section must not go on claiming a live proportion of a cap.
  it("steps back with the grid when the reads stop landing", () => {
    render(<SystemBench system={state({ online: false })} />);
    expect(screen.getByText("Cloud spend today")).toHaveStyle({ color: "var(--fg2)" });
    // Receding is losing the attention colour, not losing contrast: a graphic
    // dimmed under 3:1 would trade §5.2 for 1.4.11.
    expect(screen.getByTestId("spend-fill")).toHaveStyle({ background: "var(--fg2)" });
  });

  it("clamps a bar that has run past its cap", () => {
    const cost = { date: "2026-09-16", spend_usd: 9, cap_usd: 5 };
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost } })} />);
    expect(screen.getByTestId<HTMLElement>("spend-fill").style.width).toBe("100%");
  });

  it("draws an empty bar and says why when there is no cap to measure against", () => {
    const cost = { date: "2026-09-16", spend_usd: 1.42, cap_usd: 0, request_count: 38 };
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost } })} />);
    expect(screen.getByTestId<HTMLElement>("spend-fill").style.width).toBe("0%");
    expect(screen.getByText("$1.42 · no cap set")).toBeInTheDocument();
    // A house with no cap never meets one, so the sentence about meeting it goes.
    expect(screen.getByTestId("spend-card").textContent).not.toContain("at the cap");
    expect(screen.getByTestId("spend-card").textContent).not.toContain("NaN");
  });

  it("says nothing at all about a day the server reported no spend for", () => {
    render(<SystemBench system={state({ overview: { ...overviewFixture, cost: null } })} />);
    expect(screen.getByText("no spend recorded today")).toBeInTheDocument();
    expect(screen.getByTestId<HTMLElement>("spend-fill").style.width).toBe("0%");
    // Nothing else to say, so the bar is named by the amount alone rather than
    // pointing at an id that is not on the page.
    expect(screen.getByRole("img", { name: "no spend recorded today" })).toBeInTheDocument();
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

  it("asks for quiet to end when it is already on", () => {
    const system = state({ quiet: quiet({ active: true }) });
    render(<SystemBench system={system} />);
    expect(dnd()).toBeChecked();
    fireEvent.click(dnd());
    expect(system.quiet.set).toHaveBeenCalledWith(false, null);
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

  // The description is the entire reason for `aria-disabled` over `disabled`,
  // and it has to name both lines: what the switch is set to, and what this
  // client's last write did.
  it("describes itself with the state it is in and the news about the last write", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(described(dnd())).toEqual(["off · urgent still speaks regardless"]);

    rerender(<SystemBench system={state({ quiet: quiet({ error: DND_UNCONFIRMED }) })} />);
    expect(described(dnd())).toEqual(["off · urgent still speaks regardless", DND_UNCONFIRMED]);
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
    // Settled, so it is written in the plain strong meta rather than the accent
    // a pending `queued` wears (decision 7) — but still in the type a reader is
    // meant to be able to read, because it is the only receipt for the write.
    expect(hasClass(screen.getByText("applied"), "t-meta-strong")).toBe(true);
    // The switch asks for no expiry, which is the fourth chip's request.
    expect(pressed()).toEqual(["false", "false", "false", "true"]);
  });

  // `applied` is a claim about *this client's* last write, so it expires the
  // moment that write stops describing the world — a meeting in the calendar
  // turning quiet off by another route is not something this screen applied.
  it("takes back applied when the house moves by some other route", () => {
    const set = vi.fn();
    const { rerender } = render(<SystemBench system={state({ quiet: quiet({ set }) })} />);
    fireEvent.click(dnd());
    rerender(<SystemBench system={state({ quiet: quiet({ active: true, set }) })} />);
    expect(screen.getByText("applied")).toBeInTheDocument();

    rerender(<SystemBench system={state({ quiet: quiet({ active: false, set }) })} />);
    expect(screen.queryByText("applied")).not.toBeInTheDocument();
  });

  // Settling on `active` alone would call a different expiry applied: the
  // switch was already on, so only the `until` moved.
  it("waits for the expiry it asked for, not merely for quiet to be on", () => {
    const set = vi.fn();
    const active = { active: true, until: UNTIL_TONIGHT, set };
    const { rerender } = render(<SystemBench system={state({ quiet: quiet(active) })} />);
    fireEvent.click(chips()[1]);
    expect(set).toHaveBeenCalledWith(true, NEXT_NOON);

    rerender(<SystemBench system={state({ quiet: quiet(active) })} />);
    expect(screen.queryByText("applied")).not.toBeInTheDocument();

    rerender(
      <SystemBench system={state({ quiet: quiet({ active: true, until: NEXT_NOON, set }) })} />,
    );
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

  it("says what quiet means, in each of the states it has", () => {
    const { rerender } = render(<SystemBench system={state()} />);
    expect(screen.getByText("off · urgent still speaks regardless")).toBeInTheDocument();

    rerender(
      <SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_MORNING }) })} />,
    );
    // The day, not a bare clock: `until 08:30` reads the same for an expiry
    // thirteen hours behind and one eleven hours ahead.
    expect(screen.getByText("on · until 08:30 tomorrow · queue drains then")).toBeInTheDocument();

    rerender(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(
      screen.getByText("on · no expiry · queue will not drain on its own"),
    ).toBeInTheDocument();
  });

  // An expiry the house is still holding after the moment it names is not a
  // drain that is coming; it is one that has not happened.
  it("does not promise a drain at a time that has already gone by", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true, until: LAPSED }) })} />);
    expect(
      screen.getByText("on · until 08:30 earlier today · that moment has passed"),
    ).toBeInTheDocument();
    expect(screen.getByText("2 · growing")).toBeInTheDocument();
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

  // The clock at the *tap*, not the one the bench mounted with: an hour from
  // when the bench opened is not an hour from now.
  it("sends the hour from the moment it was asked for", () => {
    const system = state({ quiet: quiet({ active: true }) });
    render(<SystemBench system={system} />);
    act(() => void vi.advanceTimersByTime(61_000));
    fireEvent.click(chips()[0]);
    expect(system.quiet.set).toHaveBeenCalledWith(
      true,
      new Date(SYSTEM_NOW + 61_000 + 3_600_000).toISOString(),
    );
  });

  it("names tomorrow's 22:00 when it is asked for at exactly 22:00", () => {
    vi.setSystemTime(new Date(2026, 8, 16, 22, 0, 0));
    const system = state({ quiet: quiet({ active: true }) });
    render(<SystemBench system={system} />);
    fireEvent.click(chips()[2]);
    expect(system.quiet.set).toHaveBeenCalledWith(
      true,
      new Date(2026, 8, 17, 22, 0, 0).toISOString(),
    );
  });

  it("marks the expiry the house is holding", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_TONIGHT }) })} />);
    expect(pressed()).toEqual(["false", "false", "true", "false"]);
  });

  it("marks no expiry as the one that is set when the house holds none", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    expect(chips()[3]).toHaveAttribute("aria-pressed", "true");
  });

  // `1 h` is a rolling target: recomputing it can only ever match in the
  // millisecond it was tapped, so the chip that was asked for is remembered.
  it("marks the rolling hour once the house confirms that very instant", () => {
    const set = vi.fn();
    const { rerender } = render(
      <SystemBench system={state({ quiet: quiet({ active: true, set }) })} />,
    );
    act(() => void vi.advanceTimersByTime(61_000));
    fireEvent.click(chips()[0]);
    const asked = new Date(SYSTEM_NOW + 61_000 + 3_600_000).toISOString();
    expect(set).toHaveBeenCalledWith(true, asked);
    // The house is still holding no expiry, and says so; the hour this client
    // asked for is not marked until the house reports it.
    expect(pressed()).toEqual(["false", "false", "false", "true"]);

    rerender(<SystemBench system={state({ quiet: quiet({ active: true, until: asked, set }) })} />);
    expect(pressed()).toEqual(["true", "false", "false", "false"]);
  });

  it("shows which expiry is chosen without relying on its border alone", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true, until: UNTIL_TONIGHT }) })} />);
    const [hour, , night] = chips();
    expect(night.style.background).toBe("var(--ink)");
    expect(night.style.color).toBe("var(--paper)");
    expect(night.style.borderColor).toBe("transparent");
    expect(hour.style.background).toBe("transparent");
    expect(hour.style.color).toBe("var(--fg2)");
    // `--muted` rather than `--line`: four adjacent tap targets edged in
    // `--line` sit at 1.09:1 on this card, which is no edge at all.
    expect(hour.style.borderColor).toBe("var(--muted)");
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

  it("never calls an empty queue growing, or a queue nothing is quieting", () => {
    const { rerender } = render(
      <SystemBench system={state({ quiet: quiet({ active: true, held: 0 }) })} />,
    );
    expect(screen.getByText("0 held")).toBeInTheDocument();

    // Quiet is off, so the notifier is sending: two waiting is not two piling up.
    rerender(<SystemBench system={state({ quiet: quiet({ active: false, held: 2 }) })} />);
    expect(screen.getByText("2 held")).toBeInTheDocument();
  });

  it("never calls a single held notification a queue that is not growing", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true, held: 1 }) })} />);
    expect(screen.getByText("1 · growing")).toBeInTheDocument();
  });

  // An expiry at exactly this instant has arrived; it is not one still ahead.
  it("reads an expiry that is exactly now as one that has passed", () => {
    render(
      <SystemBench
        system={state({
          quiet: quiet({ active: true, until: new Date(SYSTEM_NOW).toISOString() }),
        })}
      />,
    );
    expect(screen.getByText(/that moment has passed$/)).toBeInTheDocument();
  });

  it("opens the held-back sheet through the Room's own callback", () => {
    const system = state();
    render(<SystemBench system={system} />);
    const button = screen.getByRole("button", { name: /Held back/ });
    // The chevron is decoration and must not be read out as part of the name.
    expect(button).toHaveAccessibleName("Held back2 held");
    fireEvent.click(button);
    expect(system.quiet.onHeld).toHaveBeenCalledTimes(1);
  });

  it("queues a drain and never claims anything was sent", () => {
    const system = state();
    const { rerender } = render(<SystemBench system={system} />);
    // The resting sentence in full: the route publishes an internal action and
    // returns, so there is never a `sent` or a `done` to be had from it.
    expect(drainNote()).toHaveTextContent(DRAIN_NOTE);
    // Nothing is queued yet, so nothing is waiting on the world (decision 7).
    expect(drainNote()).not.toHaveStyle({ color: "var(--accent-text)" });
    expect(screen.queryByText(/\bsent\b/i)).not.toBeInTheDocument();
    expect(described(screen.getByRole("button", { name: "Send them now" }))).toEqual([DRAIN_NOTE]);

    fireEvent.click(screen.getByRole("button", { name: "Send them now" }));
    expect(system.maintenance.drain).toHaveBeenCalledTimes(1);

    rerender(<SystemBench system={state({ maintenance: maintenance({ drainedAt: SYSTEM_NOW }) })} />);
    const note = screen.getByText(`queued 21:30 · ${DRAIN_TAIL}`);
    expect(note).toHaveStyle({ color: "var(--accent-text)" });
    expect(screen.queryByText(/\bsent\b/i)).not.toBeInTheDocument();
  });

  // The refusal belongs on the control that caused it. Left in a loose line at
  // the end of the card, the button's own description still reads `queued only`
  // after the POST came back 503.
  it("tells the reader who pressed it that the drain was refused", () => {
    render(
      <SystemBench
        system={state({
          maintenance: maintenance({
            drainedAt: SYSTEM_NOW,
            drainError: "503 · the notifier is not answering",
          }),
        })}
      />,
    );
    const button = screen.getByRole("button", { name: "Send them now" });
    expect(described(button)).toEqual(["503 · the notifier is not answering"]);
    expect(screen.queryByText(DRAIN_NOTE)).not.toBeInTheDocument();
    // Settled, not queued: a refusal is not a decision waiting on the world.
    expect(drainNote()).not.toHaveStyle({ color: "var(--accent-text)" });
  });

  it("owns up to the quiet it cannot see", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText(FOOTNOTE)).toHaveClass("t-meta-strong");
  });
});

describe("SystemBench · Maintenance", () => {
  it("dates the last consolidation and names the next", () => {
    render(<SystemBench system={state()} />);
    expect(screen.getByText("Nightly consolidation")).toBeInTheDocument();
    expect(screen.getByText("last 03:00 earlier today · 42 reviewed")).toBeInTheDocument();
    expect(screen.getByText("next 03:00 tomorrow")).toBeInTheDocument();
  });

  it("dates it against the bench's clock rather than against today", () => {
    render(
      <SystemBench system={state({ maintenance: maintenance({ last: YESTERDAY_RUN }) })} />,
    );
    expect(screen.getByText("last 03:00 yesterday · 42 reviewed")).toBeInTheDocument();
  });

  it("counts nothing when the house did not say what was reviewed", () => {
    render(<SystemBench system={state({ maintenance: maintenance({ reviewed: null }) })} />);
    expect(screen.getByText("last 03:00 earlier today")).toBeInTheDocument();
    expect(screen.queryByText(/reviewed/)).not.toBeInTheDocument();
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
    expect(described(button)).toEqual([RUN_NOTE]);
    expect(screen.getByText(RUN_NOTE)).not.toHaveStyle({ color: "var(--accent-text)" });
    fireEvent.click(button);
    expect(system.maintenance.run).toHaveBeenCalledTimes(1);

    rerender(<SystemBench system={state({ maintenance: maintenance({ ranAt: SYSTEM_NOW }) })} />);
    expect(screen.getByRole("button", { name: "Run again" })).toBeInTheDocument();
    const note = screen.getByText(`queued 21:30 · ${RAN_TAIL}`);
    // Queued is accent: a decision waiting on the world (decision 7).
    expect(note).toHaveStyle({ color: "var(--accent-text)" });
    expect(screen.queryByText(/\bdone\b/i)).not.toBeInTheDocument();
  });

  it("tells the reader who pressed it that the run was refused", () => {
    render(
      <SystemBench
        system={state({
          maintenance: maintenance({ ranAt: SYSTEM_NOW, runError: "409 · a run is already going" }),
        })}
      />,
    );
    const button = screen.getByRole("button", { name: "Run again" });
    expect(described(button)).toEqual(["409 · a run is already going"]);
    expect(screen.queryByText(RUN_NOTE)).not.toBeInTheDocument();
  });

  it("prints the timeout the server sent", () => {
    render(<SystemBench system={state()} />);
    const row = screen.getByText("Session idle timeout").parentElement;
    expect(hasClass(row, "min-h-14")).toBe(true);
    expect(row?.textContent).toBe("Session idle timeout30 minutes");
  });

  it("counts a one-minute timeout in the singular", () => {
    render(<SystemBench system={state({ maintenance: maintenance({ idleMinutes: 1 }) })} />);
    expect(screen.getByText("1 minute")).toBeInTheDocument();
  });

  it("prints no number at all until the server has sent one", () => {
    render(<SystemBench system={state({ maintenance: maintenance({ idleMinutes: null }) })} />);
    // Scoped to the row: `0 minutes` would be a guess wearing a number.
    expect(screen.getByText("Session idle timeout").parentElement).toHaveTextContent(
      "Session idle timeout",
    );
    expect(screen.queryByText(/\d+ minutes?$/)).not.toBeInTheDocument();
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

  // A button with no explicit type submits the form it is in. The Workshop is
  // not a form today and task 9's credential rows will be.
  it("presses nothing it is standing inside", () => {
    render(<SystemBench system={state({ quiet: quiet({ active: true }) })} />);
    for (const control of [...screen.getAllByRole("button"), ...screen.getAllByRole("switch")]) {
      expect(control).toHaveAttribute("type", "button");
    }
  });

  // `.t-meta-strong` and not `.t-meta`: the convention is that anything a
  // reader has to take on its own to trust the screen is written in the type
  // that can be read, and every one of these is load-bearing.
  it("writes every line a reader has to trust in the type that carries it", () => {
    render(
      <SystemBench
        system={state({
          quiet: quiet({ active: true, until: UNTIL_MORNING }),
          maintenance: maintenance({ drainedAt: SYSTEM_NOW, ranAt: SYSTEM_NOW }),
        })}
      />,
    );
    for (const line of [
      "bus · redis · 3 streams",
      "home assistant · 210 ms",
      SPEND_AMOUNT,
      SPEND_NOTE,
      "on · until 08:30 tomorrow · queue drains then",
      "2 held",
      `queued 21:30 · ${DRAIN_TAIL}`,
      FOOTNOTE,
      "last 03:00 earlier today · 42 reviewed",
      "next 03:00 tomorrow",
      `queued 21:30 · ${RAN_TAIL}`,
      "30 minutes",
    ]) {
      expect(hasClass(screen.getByText(line), "t-meta-strong")).toBe(true);
    }
    // The money is mono, so the digits line up against the cap beside them.
    expect(hasClass(screen.getByText(SPEND_AMOUNT), "font-mono")).toBe(true);

    for (const label of [
      "Cloud spend today",
      "Do-not-disturb",
      "Held back",
      "Send them now",
      "Nightly consolidation",
      "Session idle timeout",
    ]) {
      expect(hasClass(screen.getByText(label), "t-row")).toBe(true);
    }
  });

  it("colours the two write controls for what they are", () => {
    render(<SystemBench system={state()} />);
    // A control that looks like one more label is one nobody presses: 4.85:1
    // on `--surface` in light (`test/contrast.test.ts`).
    expect(screen.getByRole("button", { name: "Send them now" })).toHaveStyle({
      color: "var(--accent-text)",
    });
    // Outlined and in `--fg`: not the bench's primary action, and it carries
    // its own name at 13.70:1 rather than leaning on a 1.09:1 hairline.
    expect(screen.getByRole("button", { name: "Run consolidation now" })).toHaveStyle({
      color: "var(--fg)",
    });
  });

  it("says when one of its own reads is in flight", () => {
    const { container, rerender } = render(<SystemBench system={state()} />);
    const column = container.querySelector(".overflow-y-auto");
    expect(column).toHaveAttribute("aria-busy", "false");
    rerender(<SystemBench system={state({ loading: true })} />);
    expect(column).toHaveAttribute("aria-busy", "true");
  });

  it("leaves room under the last card for the home indicator", () => {
    const { container } = render(<SystemBench system={state()} />);
    const column = container.querySelector<HTMLElement>(".overflow-y-auto");
    // 40 px, the handoff's and the siblings' `pb-10` — plus the inset, which
    // this bench has no footer to pay on its behalf.
    expect(column?.style.paddingBottom).toContain("40px");
    expect(column?.style.paddingBottom).toContain("safe-area-inset-bottom");
  });
});
