import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { toEpisodicRow } from "@/lib/memory";
import { coldRow, hotRow, routine, semanticFile } from "@/test/fixtures";
import { MemoryBench } from "./MemoryBench";
import type { Memory } from "./useMemory";

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
    submit: vi.fn(),
    searching: false,
    searched: false,
    rows: [],
    model: "unknown",
    files: [],
    routines: [],
    scratchpad: null,
    openRoutine: null,
    toggleRoutine: vi.fn(),
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

describe("MemoryBench", () => {
  it("offers the four sub-tabs and hands a tap back to the hook", () => {
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
    expect(tabs[0].style.background).toBe("var(--field)");
    expect(tabs[1].style.background).toBe("transparent");
    expect(tabs[1].style.color).toBe("var(--fg2)");
    fireEvent.click(tabs[2]);
    expect(memory.setTab).toHaveBeenCalledWith("routines");
  });

  // The sentence the endpoint's `update_stats=False` exists for.
  it("says that browsing is not recall, in the words the handoff wrote", () => {
    render(<MemoryBench memory={state()} />);
    expect(screen.getByText(NOTE)).toHaveClass("t-meta-strong");
  });

  it("keeps the browsing note off the other sub-tabs", () => {
    render(<MemoryBench memory={state({ tab: "scratchpad" })} />);
    expect(screen.queryByText(NOTE)).toBeNull();
  });

  it("draws a hot row filled and a cold row hollow, each with its own meta", () => {
    render(<MemoryBench memory={state({ rows: [hot, cold] })} />);
    expect(screen.getByText("Kitchen lamp turned off")).toHaveClass("line-clamp-2");
    expect(screen.getByText("07:02 · hot · recalled 3×")).toHaveClass("t-meta-strong");
    expect(screen.getByText("07:02 · cold · never recalled")).toBeInTheDocument();
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

  it("says what the server will not tell us when a search matched nothing", () => {
    render(<MemoryBench memory={state({ searched: true, model: "ok" })} />);
    expect(screen.getByText("Nothing scored above the server's threshold.")).toBeInTheDocument();
    expect(
      screen.getByText("searched by meaning · the server does not report what it rejected"),
    ).toHaveClass("t-meta-strong");
  });

  it("tells an empty store from an empty search", () => {
    render(<MemoryBench memory={state()} />);
    expect(screen.getByText("No episodic memories yet.")).toBeInTheDocument();
    expect(screen.queryByText("Nothing scored above the server's threshold.")).toBeNull();
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
  // we were told off the screen.
  it("keeps the rows on screen while saying the embedder is down", () => {
    render(<MemoryBench memory={state({ model: "503", rows: [hot, cold] })} />);
    expect(
      screen.getByText("The embedder is not answering, so meaning search is off. Browsing still works."),
    ).toBeInTheDocument();
    expect(screen.getByText("Kitchen lamp turned off")).toBeInTheDocument();
    expect(screen.getByText("Asked about the dentist")).toBeInTheDocument();
  });

  it("marks the list busy while a search is in flight", () => {
    render(<MemoryBench memory={state({ rows: [hot], searching: true })} />);
    expect(screen.getByRole("list")).toHaveAttribute("aria-busy", "true");
  });

  it("names each semantic file, when it was rewritten, and what is in it", () => {
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile()] })} />);
    expect(screen.getByText("food.md")).toBeInTheDocument();
    expect(screen.getByText("preferences · modified 07:02")).toHaveClass("t-meta-strong");
    expect(screen.getByText(/No coriander/)).toBeInTheDocument();
  });

  it("clamps a long semantic file until it is asked to show all of it", () => {
    const content = Array.from({ length: 20 }, (_, line) => `line ${line}`).join("\n");
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile({ content })] })} />);
    const body = screen.getByText(/line 19/);
    expect(body).toHaveClass("line-clamp-[12]");
    const show = screen.getByRole("button", { name: "Show all" });
    expect(show).toHaveClass("min-h-11");
    fireEvent.click(show);
    expect(body).not.toHaveClass("line-clamp-[12]");
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
  });

  it("offers no Show all for a file that fits", () => {
    render(<MemoryBench memory={state({ tab: "semantic", files: [semanticFile()] })} />);
    expect(screen.queryByRole("button", { name: "Show all" })).toBeNull();
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
  it("states the pending queue even when it is empty", () => {
    render(
      <MemoryBench
        memory={state({ tab: "scratchpad", scratchpad: { content: "notes", pending_queue: 0 } })}
      />,
    );
    expect(screen.getByText("0 in the queue")).toHaveClass("t-meta-strong");
    expect(screen.getByText("notes")).toBeInTheDocument();
  });

  it("says when the scratchpad is empty, and counts what is waiting for it", () => {
    render(
      <MemoryBench
        memory={state({ tab: "scratchpad", scratchpad: { content: "", pending_queue: 14 } })}
      />,
    );
    expect(screen.getByText("14 in the queue")).toBeInTheDocument();
    expect(screen.getByText("The scratchpad is empty.")).toBeInTheDocument();
  });

  it("shows the read that failed without a region of its own to say it in", () => {
    render(<MemoryBench memory={state({ error: "Vector search unavailable" })} />);
    const line = screen.getByText("Vector search unavailable");
    expect(line).toHaveClass("t-meta-strong");
    // The Workshop header is the bench's live region (plan decision 8); a second
    // one here would say the same thing twice.
    expect(screen.queryByRole("status")).toBeNull();
  });
});
