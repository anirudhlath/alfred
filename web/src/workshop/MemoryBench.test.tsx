import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { toEpisodicRow } from "@/lib/memory";
import { coldRow, hotRow, routine, semanticFile } from "@/test/fixtures";
import { MemoryBench } from "./MemoryBench";
import type { Memory } from "./useMemory";

/**
 * Nine in the evening of the day the memory fixtures were written. Pinned,
 * because every stamp on this bench is `hhmm` plus a day label: on the real
 * clock the same row reads "earlier today" this evening and "16 Sep" next week.
 */
const NOW = new Date(2026, 8, 16, 21, 0, 0);

/** The Librarian's last and next pass, built locally so the strings hold in any zone. */
const LAST_RUN = new Date(2026, 8, 16, 3, 0, 0).toISOString();
const NEXT_RUN = new Date(2026, 8, 17, 3, 0, 0).toISOString();

/**
 * The bench is a pure view over one state object, so its tests build that
 * object rather than a `QueryClient` — the reason `ActivityBench.test.tsx`
 * needs no providers either.
 */
function state(overrides: Partial<Memory> = {}): Memory {
  return {
    tab: "episodic",
    setTab: vi.fn(),
    query: "",
    setQuery: vi.fn(),
    submitted: "",
    submit: vi.fn(),
    searching: false,
    searched: false,
    rows: [],
    model: "unknown",
    files: [],
    routines: [],
    scratchpad: null,
    consolidation: null,
    openRoutine: null,
    toggleRoutine: vi.fn(),
    // A landed read is the normal case these tests describe; the three that
    // are about an unlanded or refused one say so.
    read: true,
    loading: false,
    error: null,
    ...overrides,
  };
}

const hot = toEpisodicRow(hotRow(), 0);
const cold = toEpisodicRow(coldRow(), 1);

const NOTE =
  "Browsing here does not count as recall. Nothing you open is kept warmer or colder for it.";

const searchField = () => screen.getByRole("searchbox", { name: "Search memory" });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("MemoryBench", () => {
  it("offers the four sub-tabs as pills and hands a tap back to the hook", () => {
    const memory = state();
    render(<MemoryBench memory={memory} />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "Episodic",
      "Semantic",
      "Routines",
      "Scratchpad",
    ]);
    expect(tabs[0]).toHaveAttribute("aria-selected", "true");
    // The handoff's pill, filled ink on paper — not the bench switcher's segment.
    expect(tabs[0]).toHaveClass("h-11");
    expect(tabs[0].style.background).toBe("var(--ink)");
    expect(tabs[0].style.color).toBe("var(--paper)");
    expect(tabs[1].style.background).toBe("transparent");
    expect(tabs[1].style.color).toBe("var(--fg2)");
    fireEvent.click(tabs[2]);
    expect(memory.setTab).toHaveBeenCalledWith("routines");
  });

  it("walks the sub-tabs on the same keyboard the bench switcher has", () => {
    const memory = state({ tab: "semantic" });
    render(<MemoryBench memory={memory} />);
    const tabs = screen.getAllByRole("tab");
    fireEvent.keyDown(tabs[1], { key: "ArrowRight" });
    expect(memory.setTab).toHaveBeenLastCalledWith("routines");
    fireEvent.keyDown(tabs[1], { key: "End" });
    expect(memory.setTab).toHaveBeenLastCalledWith("scratchpad");
    fireEvent.keyDown(tabs[1], { key: "Home" });
    expect(memory.setTab).toHaveBeenLastCalledWith("episodic");
  });

  // The sentence the endpoint's `update_stats=False` exists for.
  it("says that browsing is not recall, in the words the handoff wrote", () => {
    render(<MemoryBench memory={state()} />);
    expect(screen.getByText(NOTE)).toHaveClass("t-meta-strong");
  });

  it("opens each of the other sub-tabs with its own note", () => {
    const { rerender } = render(<MemoryBench memory={state({ tab: "semantic" })} />);
    expect(screen.queryByText(NOTE)).toBeNull();
    expect(
      screen.getByText(
        "Human-readable documents the conscious mind reads before every reply. Rewritten by the nightly consolidation.",
      ),
    ).toBeInTheDocument();
    rerender(<MemoryBench memory={state({ tab: "routines" })} />);
    expect(
      screen.getByText(
        "Patterns Alfred noticed on its own. Ignored suggestions lose confidence and slide right until archived.",
      ),
    ).toBeInTheDocument();
    rerender(<MemoryBench memory={state({ tab: "scratchpad" })} />);
    expect(
      screen.getByText(
        "Working notes Alfred keeps between consolidations. The nightly pass reads them and rewrites semantic memory.",
      ),
    ).toBeInTheDocument();
  });

  it("draws a hot row filled and a cold row hollow, each stamped with its day", () => {
    render(<MemoryBench memory={state({ rows: [hot, cold] })} />);
    const hotText = screen.getByText("Kitchen lamp turned off");
    expect(hotText).toHaveClass("line-clamp-2");
    expect(hotText.style.color).toBe("");
    expect(
      screen.getByText("07:02 earlier today · significance 0.70 · recalled 3× · hot"),
    ).toHaveClass("t-meta-strong");
    expect(
      screen.getByText("07:02 earlier today · significance 0.40 · never recalled · cold"),
    ).toBeInTheDocument();
    // The handoff mutes a cold row; --fg2, which reads at 15 px where --muted does not.
    expect(screen.getByText("Asked about the dentist").style.color).toBe("var(--fg2)");
    const [hotDot, coldDot] = screen.getAllByTestId("store-dot");
    // Redundancy for the eye; the meta line beside it says hot or cold in words.
    expect(hotDot).toHaveAttribute("aria-hidden", "true");
    expect(hotDot.style.background).toBe("var(--accent)");
    expect(coldDot.style.background).toBe("transparent");
    expect(coldDot.style.borderColor).toBe("var(--line)");
    // Decorative, and only when the row has any.
    expect(screen.getByText("lamp · kitchen")).toHaveClass("t-meta");
  });

  it("takes a typed word without searching for it", () => {
    const memory = state();
    render(<MemoryBench memory={memory} />);
    const field = searchField();
    expect(field).toHaveClass("text-[16px]");
    fireEvent.change(field, { target: { value: "dentist" } });
    expect(memory.setQuery).toHaveBeenCalledWith("dentist");
    expect(memory.submit).not.toHaveBeenCalled();
  });

  it("searches when the form is submitted, and does not let the page navigate", () => {
    const memory = state({ query: "dentist" });
    render(<MemoryBench memory={memory} />);
    const form = searchField().closest("form");
    expect(form).not.toBeNull();
    expect(fireEvent.submit(form as HTMLFormElement)).toBe(false);
    expect(memory.submit).toHaveBeenCalledTimes(1);
  });

  it("quotes the words that were searched, not the ones still being typed", () => {
    render(
      <MemoryBench
        memory={state({ searched: true, model: "ok", submitted: "dentist", query: "dentist app" })}
      />,
    );
    expect(screen.getByText('Nothing close enough to "dentist".')).toBeInTheDocument();
    // Deviation 1: `recall()` returns matches only, so there is no rejected
    // best score, threshold or corpus size to print.
    expect(
      screen.getByText("searched by meaning · the server does not report what it rejected"),
    ).toHaveClass("t-meta-strong");
  });

  it("tells an empty store from an empty search", () => {
    render(<MemoryBench memory={state()} />);
    expect(screen.getByText("No episodic memories yet.")).toBeInTheDocument();
    expect(screen.queryByText(/Nothing close enough/)).toBeNull();
  });

  it("greens the model pill only once a search has answered", () => {
    const { rerender } = render(<MemoryBench memory={state()} />);
    expect(screen.getByText("model: unknown").style.color).toBe("");
    rerender(<MemoryBench memory={state({ model: "ok" })} />);
    expect(screen.getByText("model: ok").style.color).toBe("var(--green-text)");
    rerender(<MemoryBench memory={state({ model: "503" })} />);
    expect(screen.getByText("model: 503").style.color).toBe("");
  });

  // The honesty case: a refused search is no reason to take the last true thing
  // we were told off the screen. And a 503 says nothing answered — not, as the
  // handoff has it, that a model is still loading.
  it("keeps the rows on screen while saying the embedder is down", () => {
    render(<MemoryBench memory={state({ model: "503", rows: [hot, cold] })} />);
    expect(screen.getByText("The embedder is not answering.")).toBeInTheDocument();
    expect(
      screen.getByText("503 · search by meaning unavailable · the list below is by recency"),
    ).toHaveClass("t-meta-strong");
    expect(screen.getByText("Kitchen lamp turned off")).toBeInTheDocument();
    expect(screen.getByText("Asked about the dentist")).toBeInTheDocument();
  });

  it("marks the panel busy while a read is in flight, wherever the list is", () => {
    render(<MemoryBench memory={state({ loading: true })} />);
    // On the panel, not the list: an empty list is not rendered at all, and a
    // search from an empty store is exactly when the signal matters most.
    expect(screen.getByRole("tabpanel")).toHaveAttribute("aria-busy", "true");
  });

  it("gives the panel a tab stop of its own, on every sub-tab", () => {
    const { rerender } = render(<MemoryBench memory={state({ tab: "scratchpad" })} />);
    expect(screen.getByRole("tabpanel")).toHaveAttribute("tabindex", "0");
    rerender(<MemoryBench memory={state({ tab: "semantic" })} />);
    expect(screen.getByRole("tabpanel")).toHaveAttribute("tabindex", "0");
  });

  it("names each semantic file, when it was rewritten, and what is in it", () => {
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile()] })} />);
    expect(screen.getByText("food.md")).toBeInTheDocument();
    // The day as well as the clock: these are rewritten weekly at the slowest.
    expect(screen.getByText("preferences · modified 07:02 earlier today")).toHaveClass(
      "t-meta-strong",
    );
    expect(screen.getByText(/No coriander/)).toBeInTheDocument();
  });

  it("clamps a long semantic file until it is asked to show all of it", () => {
    const content = Array.from({ length: 20 }, (_, line) => `line ${line}`).join("\n");
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile({ content })] })} />);
    const body = screen.getByText(/line 19/);
    expect(body.style.getPropertyValue("-webkit-line-clamp")).toBe("12");
    const show = screen.getByRole("button", { name: "Show all" });
    expect(show).toHaveClass("min-h-11");
    fireEvent.click(show);
    expect(body.style.getPropertyValue("-webkit-line-clamp")).toBe("");
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
  });

  // The wrapping half of the estimate: one line, no newline in it, far past a
  // phone column.
  it("clamps a file whose one line wraps past the clamp", () => {
    render(
      <MemoryBench
        memory={state({ tab: "semantic", files: [semanticFile({ content: "x".repeat(800) })] })}
      />,
    );
    expect(screen.getByRole("button", { name: "Show all" })).toBeInTheDocument();
  });

  it("offers no Show all for a file that fits", () => {
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile()] })} />);
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
    expect(screen.getByText(/No coriander/).style.getPropertyValue("-webkit-line-clamp")).toBe("");
  });

  it("says when there are no semantic files at all", () => {
    render(<MemoryBench memory={state({ tab: "semantic" })} />);
    expect(screen.getByText("No semantic memory files yet.")).toBeInTheDocument();
  });

  it("renders one routine per row and opens the one the hook says is open", () => {
    const memory = state({
      tab: "routines",
      routines: [routine(), routine({ name: "morning-brief", state: "candidate" })],
      openRoutine: "morning-brief",
    });
    render(<MemoryBench memory={memory} />);
    // The open row's steps are `li`s too, so the routines are this list's
    // own children rather than every listitem under it.
    const rows = Array.from(screen.getByRole("list", { name: "Routines" }).children) as HTMLElement[];
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole("button", { expanded: false })).toBeInTheDocument();
    expect(within(rows[1]).getByRole("button", { expanded: true })).toBeInTheDocument();
    fireEvent.click(within(rows[0]).getByRole("button", { expanded: false }));
    expect(memory.toggleRoutine).toHaveBeenCalledWith("evening-lights");
  });

  it("says when nothing has been learned yet", () => {
    render(<MemoryBench memory={state({ tab: "routines" })} />);
    expect(screen.getByText("No routines learned yet.")).toBeInTheDocument();
  });

  // Never hidden: an empty queue is a fact about the consolidation, not an
  // absence of one.
  it("counts the queue even when it is empty, and says when the next pass is", () => {
    render(
      <MemoryBench
        memory={state({
          tab: "scratchpad",
          scratchpad: { content: "notes", pending_queue: 0 },
          consolidation: { last: LAST_RUN, next: NEXT_RUN },
        })}
      />,
    );
    expect(screen.getByText("0")).toHaveClass("t-title");
    expect(screen.getByText("episodes queued, unscored")).toHaveClass("t-meta-strong");
    expect(screen.getByText("03:00")).toBeInTheDocument();
    expect(screen.getByText("next consolidation · last 03:00 earlier today")).toBeInTheDocument();
    expect(screen.getByText("notes")).toBeInTheDocument();
  });

  it("says never run rather than inventing a stamp for a pass that has not happened", () => {
    render(
      <MemoryBench
        memory={state({
          tab: "scratchpad",
          scratchpad: { content: "", pending_queue: 14 },
          consolidation: { last: null, next: null },
        })}
      />,
    );
    expect(screen.getByText("14")).toBeInTheDocument();
    expect(screen.getByText("--:--")).toBeInTheDocument();
    expect(screen.getByText("next consolidation · last never run")).toBeInTheDocument();
    expect(screen.getByText("The scratchpad is empty.")).toBeInTheDocument();
  });

  it("says the scratchpad has not been read rather than showing a blank tab", () => {
    render(<MemoryBench memory={state({ tab: "scratchpad" })} />);
    expect(screen.getByText("The scratchpad has not been read yet.")).toBeInTheDocument();
    // Nothing to count and no schedule in hand: neither card is drawn.
    expect(screen.queryByText("episodes queued, unscored")).toBeNull();
    expect(screen.queryByText(/next consolidation/)).toBeNull();
  });

  it("announces the read that failed, in a region of its own", () => {
    render(<MemoryBench memory={state({ error: "Vector search unavailable" })} />);
    const line = screen.getByRole("status", { name: "Read errors" });
    expect(line).toHaveTextContent("Vector search unavailable");
    expect(line).toHaveClass("t-meta-strong");
    // The header's region speaks for the connection, which is still up: a 500
    // from the store with the socket live is a fact nothing else on this screen
    // would say aloud.
    expect(screen.getAllByRole("status")).toHaveLength(1);
  });

  it("keeps that region mounted and silent while the reads are landing", () => {
    render(<MemoryBench memory={state()} />);
    // VoiceOver can miss a region inserted with its text already in it, which
    // is why this one is always here and `sr-only` when it has nothing to say.
    const region = screen.getByRole("status", { name: "Read errors" });
    expect(region).toHaveTextContent("");
    expect(region).toHaveClass("sr-only");
  });

  it("does not call a store empty on a read that failed or never landed", () => {
    // Three sentences for three states, on the tab that has the most to lose:
    // an unread list, a refused read, and a store that really is empty.
    const { rerender } = render(<MemoryBench memory={state({ read: false })} />);
    expect(screen.getByText("Episodic memory has not been read yet.")).toBeInTheDocument();

    rerender(<MemoryBench memory={state({ read: false, error: "redis gone" })} />);
    expect(screen.getByText("Episodic memory could not be read.")).toBeInTheDocument();
    expect(screen.queryByText("No episodic memories yet.")).toBeNull();

    rerender(<MemoryBench memory={state()} />);
    expect(screen.getByText("No episodic memories yet.")).toBeInTheDocument();
  });

  it("says the same three things on Semantic, Routines and the scratchpad", () => {
    const { rerender } = render(
      <MemoryBench memory={state({ tab: "semantic", read: false, error: "redis gone" })} />,
    );
    expect(screen.getByText("Semantic memory could not be read.")).toBeInTheDocument();

    rerender(<MemoryBench memory={state({ tab: "routines", read: false })} />);
    expect(screen.getByText("Routines has not been read yet.")).toBeInTheDocument();

    rerender(<MemoryBench memory={state({ tab: "scratchpad", read: false, error: "redis gone" })} />);
    expect(screen.getByText("The scratchpad could not be read.")).toBeInTheDocument();

    rerender(<MemoryBench memory={state({ tab: "scratchpad", read: false })} />);
    expect(screen.getByText("The scratchpad has not been read yet.")).toBeInTheDocument();
  });
});
