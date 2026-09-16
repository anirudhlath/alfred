import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { coldRow, hotRow, routine, searchRow, semanticFile } from "@/test/fixtures";
import { useMemory } from "./useMemory";

const EPISODIC = "/api/admin/memory/episodic";
const SEMANTIC = "/api/admin/memory/semantic";
const ROUTINES = "/api/admin/memory/routines";
const SCRATCHPAD = "/api/admin/memory/scratchpad";

/** One canned reply: the status the server answers with, and the body it sends. */
interface Answer {
  status: number;
  body: unknown;
}

const ok = (body: unknown): Answer => ({ status: 200, body });

/** What `/memory/episodic?q=…` answers when the embedder is not there to ask. */
const EMBEDDER_DOWN: Answer = { status: 503, body: { detail: "Vector search unavailable" } };

let browse: Answer;
let search: Answer;
let semantic: Answer;
let routines: Answer;
let scratchpad: Answer;

/** Every URL the hook has asked for, in order. */
let calls: string[];

/** The calls to one endpoint, whatever query string they carried. */
const asked = (path: string): string[] => calls.filter((url) => url.split("?")[0] === path);

/**
 * The `q` the hook sent, decoded. Assert on this and never on the raw string:
 * `URLSearchParams` spells a space `+` and an ampersand `%26`, and the test is
 * about what the server receives, not about which encoding produced it.
 */
const qOf = (url: string): string | null => new URL(url, "http://localhost").searchParams.get("q");

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const path = url.split("?")[0];
      let answer: Answer;
      if (path === EPISODIC) answer = qOf(url) === null ? browse : search;
      else if (path === SEMANTIC) answer = semantic;
      else if (path === ROUTINES) answer = routines;
      else if (path === SCRATCHPAD) answer = scratchpad;
      // A path nobody staged is a typo in the hook, not an empty answer.
      else throw new Error(`unexpected request: ${url}`);
      return new Response(JSON.stringify(answer.body), { status: answer.status });
    }),
  );
}

function renderMemory(enabled = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return renderHook(({ on }: { on: boolean }) => useMemory(on), {
    wrapper,
    initialProps: { on: enabled },
  });
}

/** Let every queued microtask run without asserting anything happened. */
const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

beforeEach(() => {
  calls = [];
  browse = ok({ entries: [hotRow(), coldRow()] });
  search = ok({ entries: [searchRow()] });
  semantic = ok({ files: [semanticFile()] });
  routines = ok({ routines: [routine(), routine({ name: "morning-coffee" })] });
  scratchpad = ok({ content: "- bins out\n", pending_queue: 0 });
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useMemory", () => {
  it("starts on episodic with an unknown model and nothing searched", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    expect(result.current.tab).toBe("episodic");
    expect(result.current.query).toBe("");
    expect(result.current.searched).toBe(false);
    // Nothing has asked the embedder anything yet, so nothing is known about it.
    expect(result.current.model).toBe("unknown");
    expect(result.current.error).toBeNull();
  });

  it("browses without a q parameter", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    expect(asked(EPISODIC)).toEqual([EPISODIC]);
    expect(result.current.rows.map((row) => row.store)).toEqual(["hot", "cold"]);
  });

  it("does not search while the query is being typed", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    act(() => result.current.setQuery("dentist & co"));

    expect(result.current.query).toBe("dentist & co");
    // Every keystroke would be a recall() that embeds the query on the GPU.
    expect(asked(EPISODIC)).toHaveLength(1);
  });

  it("searches with an encoded q when the form is submitted", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    act(() => result.current.setQuery("dentist & co"));
    act(() => result.current.submit());

    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(2));
    expect(qOf(asked(EPISODIC)[1])).toBe("dentist & co");
    // Encoded, not raw: a bare ampersand would cut the query in half.
    expect(asked(EPISODIC)[1]).not.toContain("&");
    expect(result.current.searched).toBe(true);
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(result.current.rows[0].score).toBe(0.62);
  });

  it("says it is searching only while a submitted query is in flight", async () => {
    const { result } = renderMemory();
    // A browse is not a search, however long it takes.
    expect(result.current.searching).toBe(false);
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());

    expect(result.current.searching).toBe(true);
    await waitFor(() => expect(result.current.searching).toBe(false));
  });

  it("turns the model pill ok only once a search has answered", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    expect(result.current.model).toBe("unknown");

    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());

    // In flight, nothing is proven yet.
    expect(result.current.model).toBe("unknown");
    await waitFor(() => expect(result.current.model).toBe("ok"));
  });

  it("reads a 503 as the embedder being down, and says so without losing the rows", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    const browsed = result.current.rows;

    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());

    await waitFor(() => expect(result.current.model).toBe("503"));
    expect(result.current.error).toBe("Vector search unavailable");
    // The honesty test of the whole bench: a failed search must not blank the screen.
    expect(result.current.rows).toEqual(browsed);
  });

  it("clears the 503 when a later search answers", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());
    await waitFor(() => expect(result.current.model).toBe("503"));

    search = ok({ entries: [searchRow()] });
    act(() => result.current.setQuery("lamp"));
    act(() => result.current.submit());

    await waitFor(() => expect(result.current.model).toBe("ok"));
    expect(result.current.error).toBeNull();
  });

  it("retries the same query rather than sitting on a stale failure", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());
    await waitFor(() => expect(result.current.model).toBe("503"));

    // The same words a second time: the query key does not change, so nothing
    // would move if submit only wrote state.
    search = ok({ entries: [searchRow()] });
    act(() => result.current.submit());

    await waitFor(() => expect(result.current.model).toBe("ok"));
    expect(asked(EPISODIC)).toHaveLength(3);
  });

  it("does not call the embedder dead for a 401, which is a gate", async () => {
    search = { status: 401, body: { detail: "Not authenticated" } };
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    act(() => result.current.setQuery("dentist"));
    act(() => result.current.submit());

    await waitFor(() => expect(result.current.error).toBe("Not authenticated"));
    expect(result.current.model).toBe("unknown");
  });

  it("reads nothing but episodic while the episodic tab is showing", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    // A bench nobody is looking at must not glob a directory on the server.
    expect(calls).toEqual([EPISODIC]);
    expect(result.current.files).toEqual([]);
    expect(result.current.routines).toEqual([]);
    expect(result.current.scratchpad).toBeNull();
  });

  it("reads a tab's own endpoint when the tab changes to it", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    act(() => result.current.setTab("semantic"));
    await waitFor(() => expect(result.current.files).toHaveLength(1));
    expect(asked(SEMANTIC)).toHaveLength(1);

    act(() => result.current.setTab("routines"));
    await waitFor(() => expect(result.current.routines).toHaveLength(2));
    expect(asked(ROUTINES)).toHaveLength(1);

    act(() => result.current.setTab("scratchpad"));
    await waitFor(() => expect(result.current.scratchpad?.content).toBe("- bins out\n"));
    expect(asked(SCRATCHPAD)).toHaveLength(1);
  });

  it("reads nothing at all when the bench is not showing", async () => {
    const { result } = renderMemory(false);
    await settle();

    expect(calls).toEqual([]);
    expect(result.current.loading).toBe(false);
    expect(result.current.rows).toEqual([]);
  });

  it("keeps the tab across a disable and re-enable", async () => {
    const { result, rerender } = renderMemory();
    act(() => result.current.setTab("routines"));
    await waitFor(() => expect(result.current.routines).toHaveLength(2));

    rerender({ on: false });
    rerender({ on: true });

    expect(result.current.tab).toBe("routines");
    expect(result.current.routines).toHaveLength(2);
  });

  it("opens one routine at a time, and closes the open one", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    expect(result.current.openRoutine).toBeNull();
    act(() => result.current.toggleRoutine("evening-lights"));
    expect(result.current.openRoutine).toBe("evening-lights");
    act(() => result.current.toggleRoutine("morning-coffee"));
    expect(result.current.openRoutine).toBe("morning-coffee");
    act(() => result.current.toggleRoutine("morning-coffee"));
    expect(result.current.openRoutine).toBeNull();
  });

  it("reports the failure of the read the tab is showing", async () => {
    browse = { status: 500, body: { detail: "redis gone" } };
    const { result } = renderMemory();

    await waitFor(() => expect(result.current.error).toBe("redis gone"));
    // A browse that failed says nothing about the embedder: it embeds nothing.
    expect(result.current.model).toBe("unknown");
  });

  it("drops a failed tab's error when the next tab reads cleanly", async () => {
    browse = { status: 500, body: { detail: "redis gone" } };
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.error).toBe("redis gone"));

    act(() => result.current.setTab("semantic"));

    await waitFor(() => expect(result.current.files).toHaveLength(1));
    expect(result.current.error).toBeNull();
  });

  it("is loading only while a read is in flight", async () => {
    const { result } = renderMemory();
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.rows).toHaveLength(2);
  });

  it("does not poll: memory changes at consolidation speed, not at chat speed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });

    expect(asked(EPISODIC)).toHaveLength(1);
  });
});
