import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { QUERY_DEFAULTS } from "@/shell/QueryProvider";
import { coldRow, hotRow, overviewFixture, routine, searchRow, semanticFile } from "@/test/fixtures";
import { MemoryBench } from "./MemoryBench";
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

interface Gate {
  promise: Promise<void>;
  release: () => void;
}

function defer(): Gate {
  let release = (): void => {};
  const promise = new Promise<void>((resolve) => {
    release = () => resolve();
  });
  return { promise, release };
}

/**
 * While set, a *search* is held open until the test releases it — the only way
 * to observe the list mid-flight, which is where "pending" and "failed" have to
 * be told apart.
 */
let held: Gate | null = null;

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      calls.push(url);
      const path = url.split("?")[0];
      const searching = path === EPISODIC && qOf(url) !== null;
      if (searching && held) await held.promise;
      let answer: Answer;
      if (path === EPISODIC) answer = searching ? search : browse;
      else if (path === SEMANTIC) answer = semantic;
      else if (path === ROUTINES) answer = routines;
      else if (path === SCRATCHPAD) answer = scratchpad;
      // A path nobody staged is a typo in the hook, not an empty answer.
      else throw new Error(`unexpected request: ${url}`);
      return new Response(JSON.stringify(answer.body), { status: answer.status });
    }),
  );
}

/**
 * A client on the app's own policy, as `useSystem.test.tsx` builds one. A test
 * client with its own `retry: false` proves the harness's default rather than
 * the source's: the one retry this hook gets on a 5xx — and the one it is
 * denied on a 4xx — are both invisible under it, and the 503 the model pill is
 * set from is a 5xx. Only the backoff is the harness's; a real 1 s wait between
 * two attempts buys the assertions nothing but seconds.
 */
function renderMemory(enabled = true) {
  const client = new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(({ on }: { on: boolean }) => useMemory(on), {
    wrapper,
    initialProps: { on: enabled },
  });
  return { ...view, client };
}

/** Let every queued microtask run without asserting anything happened. */
const settle = async (): Promise<void> => {
  await act(async () => {
    await Promise.resolve();
  });
};

/** Type a query and submit it, as the bench's form does. */
function searchFor(result: { current: { setQuery: (q: string) => void; submit: () => void } }, text: string): void {
  act(() => result.current.setQuery(text));
  act(() => result.current.submit());
}

beforeEach(() => {
  calls = [];
  held = null;
  browse = ok({ entries: [hotRow(), coldRow()] });
  search = ok({ entries: [searchRow()] });
  semantic = ok({ files: [semanticFile()] });
  routines = ok({ routines: [routine(), routine({ name: "morning-coffee" })] });
  scratchpad = ok({ content: "- bins out\n", pending_queue: 0 });
  stubFetch();
});

afterEach(() => {
  held?.release();
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

    searchFor(result, "dentist & co");

    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(2));
    expect(qOf(asked(EPISODIC)[1])).toBe("dentist & co");
    // Encoded, not raw: a bare ampersand would cut the query in half.
    expect(asked(EPISODIC)[1]).not.toContain("&");
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    expect(result.current.rows[0].score).toBe(0.62);
    expect(result.current.searched).toBe(true);
  });

  it("says it is searching only while a submitted query is in flight", async () => {
    const { result } = renderMemory();
    // A browse is not a search, however long it takes.
    expect(result.current.searching).toBe(false);
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    searchFor(result, "dentist");

    expect(result.current.searching).toBe(true);
    // A first search has no matches to hold over, so the browse stays under it
    // — and is not claimed as a result while it does.
    expect(result.current.rows).toHaveLength(2);
    expect(result.current.searched).toBe(false);
    await waitFor(() => expect(result.current.searching).toBe(false));
  });

  it("turns the model pill ok only once a search has answered", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    expect(result.current.model).toBe("unknown");

    searchFor(result, "dentist");

    // In flight, nothing is proven yet.
    expect(result.current.model).toBe("unknown");
    await waitFor(() => expect(result.current.model).toBe("ok"));
  });

  it("reads a 503 as the embedder being down, and says so without losing the rows", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    const browsed = result.current.rows;

    searchFor(result, "dentist");

    await waitFor(() => expect(result.current.model).toBe("503"));
    expect(result.current.error).toBe("Vector search unavailable");
    // The honesty test of the whole bench: a failed search must not blank the
    // screen, and must not claim the browse under it is a set of matches.
    expect(result.current.rows).toEqual(browsed);
    expect(result.current.searched).toBe(false);
  });

  it("calls a search that matched nothing a search all the same", async () => {
    search = ok({ entries: [] });
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    searchFor(result, "dentist");

    // The bench draws two different empty states off this flag; an empty answer
    // is the one that says the server rejected everything it scored.
    await waitFor(() => expect(result.current.searched).toBe(true));
    expect(result.current.rows).toEqual([]);
  });

  it("holds the matches already on screen while the next search is in flight", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.rows).toHaveLength(1));
    const matches = result.current.rows;

    held = defer();
    search = ok({ entries: [searchRow({ id: "ep-92", summary: "Booked the hygienist" })] });
    searchFor(result, "hygienist");
    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(3));

    // Pending is not failed: the list holds the last matches rather than
    // flashing the browse between one answer and the next.
    expect(result.current.rows).toEqual(matches);
    expect(result.current.searched).toBe(true);
    expect(result.current.searching).toBe(true);

    held.release();
    await waitFor(() => expect(result.current.rows[0].id).toBe("ep-92"));
  });

  it("drops back to the browse when the next search fails, rather than showing stale matches", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    const browsed = result.current.rows;
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.rows).toHaveLength(1));

    search = EMBEDDER_DOWN;
    searchFor(result, "hygienist");

    await waitFor(() => expect(result.current.model).toBe("503"));
    // Matches for words nobody asked about are worse than no matches.
    expect(result.current.rows).toEqual(browsed);
    expect(result.current.searched).toBe(false);
  });

  it("clears the 503 when a later search answers", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.model).toBe("503"));

    search = ok({ entries: [searchRow()] });
    searchFor(result, "lamp");

    await waitFor(() => expect(result.current.model).toBe("ok"));
    expect(result.current.error).toBeNull();
  });

  it("reads a 503 as the embedder dying, even after one search had answered", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.model).toBe("ok"));

    // The pill is what the *last* search learned, not what the first one did.
    search = EMBEDDER_DOWN;
    searchFor(result, "lamp");

    await waitFor(() => expect(result.current.model).toBe("503"));
    expect(result.current.error).toBe("Vector search unavailable");
  });

  it("retries the same query rather than sitting on a stale failure", async () => {
    search = EMBEDDER_DOWN;
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.model).toBe("503"));

    // The same words a second time: the query key does not change, so nothing
    // would move if submit only wrote state.
    search = ok({ entries: [searchRow()] });
    act(() => result.current.submit());

    await waitFor(() => expect(result.current.model).toBe("ok"));
    // Four attempts, not three: the browse, the refused search *twice* —
    // `QUERY_DEFAULTS` retries a 5xx once, and a 503 from the embedder is a
    // 5xx — and the resubmission that answered. Under a test-local
    // `retry: false` this read three, which was the harness's policy and not
    // the app's.
    expect(asked(EPISODIC)).toHaveLength(4);
  });

  it("browses when a blank query is submitted", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    searchFor(result, "   ");

    // Whitespace is not a question worth embedding on the GPU.
    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(2));
    expect(asked(EPISODIC)).toEqual([EPISODIC, EPISODIC]);
    expect(result.current.searched).toBe(false);
    expect(result.current.rows).toHaveLength(2);
  });

  it("goes back to the browse when the search is cleared", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    const browsed = result.current.rows;
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.searched).toBe(true));

    searchFor(result, "");

    await waitFor(() => expect(result.current.searched).toBe(false));
    expect(result.current.rows).toEqual(browsed);
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

  it("keeps the tab, the query and the open routine across a disable and re-enable", async () => {
    const { result, rerender } = renderMemory();
    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.searched).toBe(true));
    act(() => result.current.toggleRoutine("evening-lights"));
    act(() => result.current.setTab("routines"));
    await waitFor(() => expect(result.current.routines).toHaveLength(2));

    rerender({ on: false });
    rerender({ on: true });

    // The hook lives in the panel, not in the bench, so a trip to Triggers and
    // back comes home to the screen you left.
    expect(result.current.tab).toBe("routines");
    expect(result.current.query).toBe("dentist");
    expect(result.current.searched).toBe(true);
    expect(result.current.openRoutine).toBe("evening-lights");
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
    // A 503, the embedder's own status, on the read that never embeds anything:
    // only a search is evidence about the model, whatever the browse answers.
    browse = { status: 503, body: { detail: "Memory is not answering" } };
    const { result } = renderMemory();

    await waitFor(() => expect(result.current.error).toBe("Memory is not answering"));
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

  it("complains about nothing while the bench is not showing", async () => {
    browse = { status: 500, body: { detail: "redis gone" } };
    const { result, rerender } = renderMemory();
    await waitFor(() => expect(result.current.error).toBe("redis gone"));

    rerender({ on: false });

    // Every read is idle behind a closed bench; a cached error is a complaint
    // about a screen nobody is looking at.
    expect(result.current.error).toBeNull();
  });

  it("is loading while a read is in flight, and not once it has settled", async () => {
    const { result } = renderMemory();
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.rows).toHaveLength(2);
  });

  it("re-reads the showing tab when the app comes back to the foreground", async () => {
    const { result, client } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    // What ConnectionProvider's REHYDRATE_KEYS does on visibilitychange. The
    // key is a prefix: one entry has to reach all four reads.
    await act(() => client.invalidateQueries({ queryKey: ["memory"] }));

    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(2));
    // …and only the read the bench is showing. Invalidation marks the other
    // three stale without running them, so returning to the app does not glob
    // a directory for a tab nobody has opened.
    expect(calls).toEqual([EPISODIC, EPISODIC]);
  });

  it("re-reads nothing on the way back when the bench is not showing", async () => {
    const { client } = renderMemory(false);
    await settle();

    await act(() => client.invalidateQueries({ queryKey: ["memory"] }));
    await settle();

    expect(calls).toEqual([]);
  });

  it("keeps the query the rows answer, not the one still being typed", async () => {
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    expect(result.current.submitted).toBe("");

    searchFor(result, "dentist");
    await waitFor(() => expect(result.current.searched).toBe(true));
    expect(result.current.submitted).toBe("dentist");

    // The empty state quotes `submitted`, so typing on after a search must not
    // rewrite the sentence under rows that answered the older words.
    act(() => result.current.setQuery("dentist appointment"));
    expect(result.current.submitted).toBe("dentist");
  });

  it("reads the Librarian's schedule off the overview without asking for it", async () => {
    const { result, client } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));
    // Nothing has polled the overview in this test, so there is nothing to say.
    expect(result.current.consolidation).toBeNull();

    // What the Room and the Workshop's header put there on their own cadence.
    await act(() => {
      client.setQueryData(["overview"], {
        ...overviewFixture,
        librarian: { last_run_at: "2026-09-16T03:00:00Z", reviewed: 12, next_run_at: "2026-09-17T03:00:00Z" },
      });
      return Promise.resolve();
    });

    await waitFor(() =>
      expect(result.current.consolidation).toEqual({
        last: "2026-09-16T03:00:00Z",
        next: "2026-09-17T03:00:00Z",
      }),
    );
    // And no read of its own: the scratchpad's stat card costs one cache hit.
    expect(calls).toEqual([EPISODIC]);
  });

  it("says per tab whether that tab's read has landed", async () => {
    const { result, rerender } = renderMemory();
    // False before the answer and false after one that failed: the bench needs
    // both to keep `No episodic memories yet.` off a screen with no evidence
    // behind it.
    expect(result.current.read).toBe(false);
    await waitFor(() => expect(result.current.read).toBe(true));

    routines = { status: 500, body: { detail: "redis gone" } };
    act(() => result.current.setTab("routines"));
    rerender({ on: true });
    await waitFor(() => expect(result.current.error).toBe("redis gone"));
    // Episodic landed; Routines did not, and the flag follows the tab.
    expect(result.current.read).toBe(false);

    act(() => result.current.setTab("episodic"));
    rerender({ on: true });
    await waitFor(() => expect(result.current.read).toBe(true));
  });

  it("does not poll: memory changes at consolidation speed, not at chat speed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderMemory();
    await waitFor(() => expect(result.current.rows).toHaveLength(2));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(300_000);
    });

    expect(calls).toEqual([EPISODIC]);
  });
});

/**
 * The bench over its own hook, which is the seam neither half's tests reach:
 * `MemoryBench.test.tsx` hand-builds a `Memory` and so can only prove the view
 * draws what it is handed, and the assertions above read the hook's fields
 * rather than the sentences a reader gets. A claim made from a state the hook
 * cannot actually produce — or withheld in one it can — is invisible to both.
 */
function Bench() {
  return <MemoryBench memory={useMemory(true)} />;
}

function renderBench() {
  const client = new QueryClient({
    defaultOptions: { ...QUERY_DEFAULTS, queries: { ...QUERY_DEFAULTS.queries, retryDelay: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <Bench />
    </QueryClientProvider>,
  );
}

/** Type into the bench's own field and submit its own form. */
function searchOnScreen(text: string): void {
  const field = screen.getByRole("searchbox", { name: "Search memory" });
  fireEvent.change(field, { target: { value: text } });
  fireEvent.submit(field.closest("form") as HTMLFormElement);
}

describe("MemoryBench over useMemory", () => {
  it("does not say a search found nothing while that search is still in flight", async () => {
    renderBench();
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2));

    // A first search that genuinely found nothing. This is the honest empty
    // state, and it is what leaves `searchQuery.data` at `[]`.
    search = ok({ entries: [] });
    searchOnScreen("zzz");
    await waitFor(() => expect(screen.getByText('Nothing close enough to "zzz".')).toBeInTheDocument());

    // A second search, held open. `keepPreviousData` hands back the first
    // search's `[]` while the new key is pending, so the rows are empty and
    // `submitted` has already moved on — the two facts that between them
    // sentence a request the server has not answered.
    held = defer();
    search = ok({ entries: [searchRow()] });
    searchOnScreen("dentist");
    await waitFor(() => expect(asked(EPISODIC)).toHaveLength(3));

    expect(screen.queryByText('Nothing close enough to "dentist".')).toBeNull();
    // Nor the other empty state, which would be a claim about a store whose
    // browse is holding two rows.
    expect(screen.queryByText("No episodic memories yet.")).toBeNull();

    held.release();
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(1));
  });

  it("does not blame the browse for a search the embedder refused", async () => {
    browse = ok({ entries: [] });
    renderBench();
    await waitFor(() => expect(screen.getByText("No episodic memories yet.")).toBeInTheDocument());

    search = EMBEDDER_DOWN;
    searchOnScreen("dentist");
    await waitFor(() =>
      expect(screen.getByText("The embedder is not answering.")).toBeInTheDocument(),
    );

    // The browse was read, and it is what the list is showing. The refusal is
    // news about the search, and the region above says so.
    expect(screen.getByText("No episodic memories yet.")).toBeInTheDocument();
    expect(screen.queryByText("Episodic memory could not be read.")).toBeNull();
    expect(screen.getByLabelText("Read errors")).toHaveTextContent("Vector search unavailable");
  });
});
