import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Trigger } from "@/lib/triggers";
import { trigger } from "@/test/fixtures";
import { REREAD_MS, useTriggers } from "./useTriggers";

const LIST = "/api/admin/triggers";
const enabledPath = (id: string) => `${LIST}/${id}/enabled`;
const firePath = (id: string) => `${LIST}/${id}/fire`;

/** A time record: a `run_at` and no cron, off — the row every toggle test taps. */
const BINS: Trigger = trigger({ trigger_id: "trg_bins", name: "Bins out", enabled: false });

/** A schedule: the same stored `trigger_type`, armed with a cron instead. */
const WEEKLY: Trigger = trigger({
  trigger_id: "trg_weekly",
  name: "Weekly review",
  conditions: { cron: "0 19 * * 4", run_at: null },
});

const DOOR: Trigger = trigger({
  trigger_id: "trg_door",
  trigger_type: "sensor",
  name: "Front door",
  conditions: { entity_id: "binary_sensor.front_door", state_match: "on" },
});

/** One canned reply: the status the server answers with, and the body it sends. */
interface Answer {
  status: number;
  body: unknown;
}

/**
 * What both controls really answer. Not `{"applied": true}` — the route
 * publishes to the actions stream and returns; the triggers process picks the
 * change up inside its own cache window, which is what `REREAD_MS` is timing.
 */
const QUEUED: Answer = { status: 200, body: { status: "queued", effective_within_seconds: 60 } };

/** One request the hook made. */
interface Call {
  url: string;
  method: string;
  body: unknown;
}

let calls: Call[];
/** What `GET /api/admin/triggers` answers next; tests move it to prove a re-read. */
let list: Answer;
/** What a POST to a given path answers. Anything unstaged is queued. */
let replies: Record<string, Answer>;

const reads = (): string[] => calls.filter((call) => call.method === "GET").map((call) => call.url);
const posts = (): string[] => calls.filter((call) => call.method === "POST").map((call) => call.url);

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? "GET";
      const raw = init?.body;
      calls.push({
        url,
        method,
        body: typeof raw === "string" ? (JSON.parse(raw) as unknown) : null,
      });
      const answer = method === "GET" ? list : (replies[url] ?? QUEUED);
      return new Response(JSON.stringify(answer.body), { status: answer.status });
    }),
  );
}

const makeClient = (): QueryClient =>
  new QueryClient({ defaultOptions: { queries: { retry: false } } });

/**
 * `client` is shared by the two mounts in the unmount test — one Workshop
 * closing and the next one opening over the same query cache, which is the only
 * place a leaked window could still be seen doing damage.
 */
function renderTriggers(enabled = true, client = makeClient()) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  const view = renderHook(({ on }: { on: boolean }) => useTriggers(on), {
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

/** Step the clock the way a phone left on the bench does. */
const wait = async (ms: number): Promise<void> => {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
};

beforeEach(() => {
  calls = [];
  replies = {};
  list = { status: 200, body: { triggers: [BINS, WEEKLY, DOOR] } };
  stubFetch();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("useTriggers", () => {
  it("reads the list once when the bench is showing", async () => {
    const { result } = renderTriggers();

    await waitFor(() => expect(result.current.triggers).toHaveLength(3));
    expect(reads()).toEqual([LIST]);
    expect(result.current.error).toBeNull();
    expect(result.current.pending).toEqual({});
    expect(result.current.fired).toEqual({});
  });

  it("reads nothing at all while the bench is not showing", async () => {
    const { result } = renderTriggers(false);
    await settle();

    expect(calls).toEqual([]);
    expect(result.current.triggers).toEqual([]);
    expect(result.current.shown).toEqual([]);
    expect(result.current.loading).toBe(false);
  });

  it("shows every trigger under the all chip", async () => {
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    expect(result.current.kind).toBe("all");
    expect(result.current.shown).toEqual(result.current.triggers);
  });

  it("filters the list to one kind, reading the chip off the record", async () => {
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.setKind("schedule"));

    // Both are stored `trigger_type: "time"`; only the cron one is a schedule.
    expect(result.current.shown.map((row) => row.trigger_id)).toEqual(["trg_weekly"]);
    act(() => result.current.setKind("time"));
    expect(result.current.shown.map((row) => row.trigger_id)).toEqual(["trg_bins"]);
    act(() => result.current.setKind("sensor"));
    expect(result.current.shown.map((row) => row.trigger_id)).toEqual(["trg_door"]);
    act(() => result.current.setKind("composite"));
    expect(result.current.shown).toEqual([]);
    // The unfiltered list never moves — the bench needs it for the `all` count
    // and to tell an empty house from an empty filter.
    expect(result.current.triggers).toHaveLength(3);
  });

  it("keeps the chip and the open row across a disable and re-enable", async () => {
    const { result, rerender } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));
    act(() => result.current.setKind("sensor"));
    act(() => result.current.toggleOpen("trg_door"));

    rerender({ on: false });
    rerender({ on: true });

    // The hook lives in the panel, not in the bench, so a trip to Memory and
    // back comes home to the screen you left.
    expect(result.current.kind).toBe("sensor");
    expect(result.current.open).toBe("trg_door");
  });

  it("expands one trigger at a time, and closes the open one", async () => {
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    expect(result.current.open).toBeNull();
    act(() => result.current.toggleOpen("trg_bins"));
    expect(result.current.open).toBe("trg_bins");
    act(() => result.current.toggleOpen("trg_weekly"));
    expect(result.current.open).toBe("trg_weekly");
    act(() => result.current.toggleOpen("trg_weekly"));
    expect(result.current.open).toBeNull();
  });

  it("marks a toggled row enabling without moving its switch", async () => {
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));
    const before = Date.now();

    act(() => result.current.toggle(BINS));

    await waitFor(() => expect(posts()).toEqual([enabledPath("trg_bins")]));
    expect(result.current.pending.trg_bins.kind).toBe("enabling");
    expect(result.current.pending.trg_bins.at).toBeGreaterThanOrEqual(before);
    expect(result.current.pending.trg_bins.at).toBeLessThanOrEqual(Date.now());
    expect(result.current.pending.trg_bins.error).toBeUndefined();
    // Decision 6: the server said `queued`, not `applied`. The scheduler holds
    // the old setting for up to 60 s and this client does not know when it
    // stopped — so the row keeps the state the last read gave it.
    expect(result.current.triggers[0].enabled).toBe(false);
    expect(result.current.shown[0].enabled).toBe(false);
  });

  it("asks for the opposite of the state the row is showing", async () => {
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await waitFor(() => expect(posts()).toHaveLength(1));
    expect(calls.at(-1)?.body).toEqual({ enabled: true });

    act(() => result.current.toggle(WEEKLY));
    await waitFor(() => expect(posts()).toHaveLength(2));
    expect(calls.at(-1)?.body).toEqual({ enabled: false });
    expect(result.current.pending.trg_weekly.kind).toBe("disabling");
    expect(result.current.triggers[1].enabled).toBe(true);
  });

  it("re-reads the list 60 s after a toggle and drops the note", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await waitFor(() => expect(result.current.pending.trg_bins).toBeDefined());
    list = { status: 200, body: { triggers: [{ ...BINS, enabled: true }, WEEKLY, DOOR] } };

    await wait(REREAD_MS);

    await waitFor(() => expect(reads()).toHaveLength(2));
    // The window is up: the row now says what the server says, and the client's
    // note about it is gone.
    expect(result.current.pending).toEqual({});
    expect(result.current.triggers[0].enabled).toBe(true);
  });

  it("drops the note on the re-read even when the server still says the old thing", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await waitFor(() => expect(result.current.pending.trg_bins).toBeDefined());

    await wait(REREAD_MS);

    // No second window, no note that outlives its evidence (spec §5.2): the row
    // goes back to telling the truth the server last told us, whatever it is.
    await waitFor(() => expect(reads()).toHaveLength(2));
    expect(result.current.pending).toEqual({});
    expect(result.current.triggers[0].enabled).toBe(false);
  });

  it("does not re-read before the window is up", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await waitFor(() => expect(result.current.pending.trg_bins).toBeDefined());
    await wait(REREAD_MS - 1_000);

    expect(reads()).toEqual([LIST]);
    expect(result.current.pending.trg_bins.kind).toBe("enabling");
  });

  it("restarts the window on a second toggle rather than stacking timers", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await wait(30_000);
    act(() => result.current.toggle(BINS));
    await wait(31_000);

    // 61 s since the first tap, 31 s since the second: the window belongs to
    // the last thing this client asked for.
    expect(reads()).toEqual([LIST]);
    expect(result.current.pending.trg_bins).toBeDefined();

    await wait(30_000);

    await waitFor(() => expect(reads()).toHaveLength(2));
    // One re-read, not two: the first timer was cleared, never stacked.
    expect(reads()).toHaveLength(2);
    expect(result.current.pending).toEqual({});
  });

  it("records a failed toggle on the row that failed, not on the bench", async () => {
    replies[enabledPath("trg_bins")] = { status: 500, body: { detail: "condition JSON fails to parse" } };
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));

    await waitFor(() => expect(result.current.pending.trg_bins.error).toBeDefined());
    expect(result.current.pending.trg_bins.error).toBe("condition JSON fails to parse");
    // The status, because the row says a different thing at 500 (the record
    // cannot be read) from what it says at any other refusal.
    expect(result.current.pending.trg_bins.status).toBe(500);
    expect(result.current.pending.trg_bins.kind).toBe("enabling");
    // One trigger's trouble is not the bench's: the other rows read fine.
    expect(result.current.error).toBeNull();
    // And nothing moved. A refused change is the strongest reason of all to
    // leave the row where the last read put it.
    expect(result.current.triggers[0].enabled).toBe(false);
  });

  it("marks a fire firing, stamps it, and clears the note on the same window", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));
    const before = Date.now();

    act(() => result.current.fire(BINS));

    await waitFor(() => expect(posts()).toEqual([firePath("trg_bins")]));
    expect(result.current.pending.trg_bins.kind).toBe("firing");
    await waitFor(() => expect(result.current.fired.trg_bins).toBeDefined());
    expect(result.current.fired.trg_bins).toBeGreaterThanOrEqual(before);
    expect(result.current.fired.trg_bins).toBeLessThanOrEqual(Date.now());
    // The stamp the note quotes and the stamp the button reads are one instant.
    expect(result.current.fired.trg_bins).toBe(result.current.pending.trg_bins.at);

    await wait(REREAD_MS);

    await waitFor(() => expect(reads()).toHaveLength(2));
    expect(result.current.pending).toEqual({});
    // `fired` outlives the note: the button still reads `Fire again`, because
    // this client did queue one. It never claims the trigger ran.
    expect(result.current.fired.trg_bins).toBeDefined();
  });

  it("does not stamp a fire the server refused", async () => {
    replies[firePath("trg_bins")] = { status: 404, body: { detail: "Trigger not found" } };
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.fire(BINS));

    await waitFor(() => expect(result.current.pending.trg_bins.error).toBe("Trigger not found"));
    expect(result.current.pending.trg_bins.status).toBe(404);
    // Nothing was queued, so there is nothing to call `Fire again`.
    expect(result.current.fired).toEqual({});
  });

  it("keeps two rows pending at once, independently", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    replies[enabledPath("trg_door")] = { status: 500, body: { detail: "record unreadable" } };
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    act(() => result.current.toggle(BINS));
    await wait(20_000);
    act(() => result.current.toggle(DOOR));

    await waitFor(() => expect(result.current.pending.trg_door.error).toBe("record unreadable"));
    expect(result.current.pending.trg_bins.error).toBeUndefined();
    expect(Object.keys(result.current.pending).sort()).toEqual(["trg_bins", "trg_door"]);

    // Each row's window runs from its own tap.
    await wait(40_000);
    await waitFor(() => expect(result.current.pending.trg_bins).toBeUndefined());
    expect(result.current.pending.trg_door.error).toBe("record unreadable");

    await wait(20_000);
    await waitFor(() => expect(result.current.pending).toEqual({}));
  });

  it("clears every window on unmount, leaving nothing behind to read for the next bench", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const client = makeClient();
    const first = renderTriggers(true, client);
    await waitFor(() => expect(first.result.current.triggers).toHaveLength(3));

    act(() => first.result.current.toggle(BINS));
    act(() => first.result.current.toggle(WEEKLY));
    act(() => first.result.current.fire(DOOR));
    await waitFor(() => expect(posts()).toHaveLength(3));

    first.unmount();

    // The Workshop opens again over the same query cache — the one place an
    // orphaned window would still find an active query to invalidate, and so
    // the only place the leak is visible rather than merely present.
    const second = renderTriggers(true, client);
    await waitFor(() => expect(second.result.current.triggers).toHaveLength(3));
    calls = [];

    await wait(REREAD_MS * 5);

    // Three windows open, three cleared. A leaked 60 s timer per toggle is how
    // a Home Screen app that lives for days ends up storming the API from a
    // bench nobody has open.
    expect(calls).toEqual([]);
    expect(second.result.current.pending).toEqual({});
  });

  it("does not poll: a trigger list changes when someone changes it", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { result } = renderTriggers();
    await waitFor(() => expect(result.current.triggers).toHaveLength(3));

    await wait(300_000);

    expect(reads()).toEqual([LIST]);
  });

  it("reports a failed list read, and nothing at all once the bench is not showing", async () => {
    list = { status: 500, body: { detail: "redis gone" } };
    const { result, rerender } = renderTriggers();

    await waitFor(() => expect(result.current.error).toBe("redis gone"));
    expect(result.current.triggers).toEqual([]);

    rerender({ on: false });

    // The read is idle behind a closed bench; a cached error is a complaint
    // about a screen nobody is looking at.
    expect(result.current.error).toBeNull();
  });

  it("is loading while the read is in flight, and not once it has settled", async () => {
    const { result } = renderTriggers();
    expect(result.current.loading).toBe(true);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.triggers).toHaveLength(3);
  });
});
