import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { notifyManager, useQuery, useQueryClient, type QueryState } from "@tanstack/react-query";
import { ApiError, errorText } from "@/lib/api";
import {
  fetchTriggers,
  fireTrigger,
  setTriggerEnabled,
  triggerKind,
  type Trigger,
  type TriggerKind,
} from "@/lib/triggers";

/**
 * How long after a request the list is read again, and how long a row's note
 * stands before that read can take it down. `POST …/enabled` answers
 * `{"status":"queued","effective_within_seconds":60}`: the route publishes to
 * the actions stream and the triggers process applies the change inside its
 * own 60 s cache window. Nothing tells this client when that happened — so the
 * row waits out the window with a note on it, and then the list is read again
 * and the note comes down with the rows that read brings back.
 *
 * A *refused* request queued nothing and the list cannot have changed for it,
 * and it takes the same window all the same: the failure note is a client claim
 * like any other, and this is how long any of them may stand before the server
 * speaks for the row again (spec §5.2).
 */
export const REREAD_MS = 60_000;

/**
 * How long a read stays fresh. Well under the window above, so the re-read that
 * closes it always goes to the server, and long enough that a trip to Memory
 * and back re-uses what is already in hand.
 */
const STALE_MS = 30_000;

const TRIGGERS_KEY = ["triggers"] as const;

/**
 * Every read of the list that has landed, answered or refused. A closed
 * window's note waits for this number to move. A refusal counts: the bench then
 * says the list could not be read, which is the server speaking for the row too,
 * and a note held up until some later success would stand on nothing.
 */
const landedReads = (state: QueryState | undefined): number =>
  (state?.dataUpdateCount ?? 0) + (state?.errorUpdateCount ?? 0);

/** What the chips filter by: a kind, or every kind at once. */
export type KindFilter = "all" | TriggerKind;

/** Which control is waiting, in the present tense the row prints. */
export type PendingKind = "enabling" | "disabling" | "firing";

/**
 * A request this client has sent and cannot yet see the effect of. One per row,
 * standing for `REREAD_MS` and then until the read that window asks for has
 * landed — a client claim must not outlive the evidence for it (spec §5.2), and
 * after the window the list itself is the evidence. Not a moment less, either:
 * the note leaves in the render that draws that read, because a note dropped
 * when the read was *asked for* left the row, for a round trip, showing the
 * state the tap meant to change with nothing under it to say so.
 */
export interface Pending {
  kind: PendingKind;
  /** When this client sent it — the note's `queued 21:15`. */
  at: number;
  /**
   * The server's refusal, when it refused. The note becomes a failure sentence
   * and the row keeps the state the last read gave it, which it never left.
   */
  error?: string;
  /**
   * The status behind that refusal. Present only when a server answered: a
   * request that never arrived has an `error` and no status, and the row must
   * not print a number nobody sent. 500 is its own case — the record cannot be
   * read — so the row needs the number, not just the sentence.
   */
  status?: number;
}

/**
 * A note as the hook holds it. `closed` is set when its window closes, to
 * `landedReads` at that moment: the note comes down in the first render whose
 * count differs — any read landing since, or a cache cleared under it.
 */
interface Note extends Pending {
  closed?: number;
}

/** The notes whose windows have not closed, or whose closing read has not landed. */
const standing = (notes: Record<string, Note>, landed: number): Record<string, Note> =>
  Object.fromEntries(
    Object.entries(notes).filter(([, note]) => note.closed === undefined || note.closed === landed),
  );

export interface Triggers {
  kind: KindFilter;
  setKind: (kind: KindFilter) => void;
  /** Every trigger the last read returned — the `all` count, and the empty house. */
  triggers: Trigger[];
  /** The ones the chip lets through; an empty `shown` under a full list is an empty filter. */
  shown: Trigger[];
  /** The one expanded row's `trigger_id`. */
  open: string | null;
  toggleOpen: (id: string) => void;
  /**
   * Rows with a request in flight, inside its window, or waiting on the read
   * that window asked for, by `trigger_id`.
   */
  pending: Record<string, Pending>;
  /** Queue the opposite of what the row is showing. The row does not move. */
  toggle: (trigger: Trigger) => void;
  /** Queue a fire. Nothing here learns whether it ran; the events stream does. */
  fire: (trigger: Trigger) => void;
  /**
   * When this client last queued a fire for a row, by `trigger_id` — so the
   * button can read `Fire again`. Never evidence that the trigger fired: only
   * `trigger.fired` on the events stream is that.
   */
  fired: Record<string, number>;
  /** The list read is in flight. */
  loading: boolean;
  /**
   * A read of the list has landed at least once: `dataUpdatedAt !== 0`, so a
   * first read that fails leaves it false and no later failure takes it back.
   * It is the flag `useMemory` and `useSystem`'s sections each carry, for the
   * same reason and by the same derivation. `loading` cannot stand in for it:
   * react-query's default `networkMode: "online"` **pauses** a read only once
   * its `onlineManager` has heard the phone go offline, and a paused query is
   * not fetching, holds no data and raises no error. (On a launch that began
   * offline the read runs and fails instead; either way nothing has landed,
   * which is what this flag says.) Without this flag the bench opens on a
   * phone with no signal by asserting `No triggers yet.` about a house it has
   * never asked — a claim about the server made from no evidence, which spec
   * §5.2 forbids. `dataUpdatedAt` and not `isSuccess`, because the question is
   * "has a read ever landed", not "did the last attempt succeed".
   */
  read: boolean;
  /** Why the list read failed, if it did. Null while the bench is not showing. */
  error: string | null;
}

/**
 * The Triggers bench's one hook: one read, two fire-and-forget controls, and a
 * 60 s window in which the client knows a request landed and does not know the
 * trigger's state.
 *
 * `enabled` is `bench === "triggers"`. It gates the read, because a bench
 * nobody is looking at must not ask the server for anything — but the hook
 * lives in `WorkshopPanel` rather than in the bench, so the chip, the open row
 * and every running window survive a trip to Memory.
 */
export function useTriggers(enabled: boolean): Triggers {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<KindFilter>("all");
  const [open, setOpen] = useState<string | null>(null);
  const [pending, setPending] = useState<Record<string, Note>>({});
  const [fired, setFired] = useState<Record<string, number>>({});

  // No `refetchInterval`: a trigger list changes when a person or the LLM
  // changes it, never on its own, and the one moment this client has reason to
  // doubt it is the window it opened itself.
  const query = useQuery({
    queryKey: TRIGGERS_KEY,
    queryFn: fetchTriggers,
    enabled,
    staleTime: STALE_MS,
  });

  // Read the way `useIsFetching` reads the cache. `useQuery` builds its result
  // from the live query state at render, and so does this snapshot, so the
  // render that draws a re-read's rows is the render that sees its count move.
  const subscribe = useCallback(
    (onChange: () => void) =>
      queryClient.getQueryCache().subscribe(notifyManager.batchCalls(onChange)),
    [queryClient],
  );
  const landed = useSyncExternalStore(subscribe, () =>
    landedReads(queryClient.getQueryState(TRIGGERS_KEY)),
  );

  // A closed window's note comes down in the render that draws the read it was
  // waiting for. Adjusted during render rather than filtered on the way out, so
  // a dropped note is gone from state too and a later count can never bring it
  // back.
  if (Object.values(pending).some((note) => note.closed !== undefined && note.closed !== landed)) {
    setPending((current) => standing(current, landed));
  }

  // One window per row. A `ref` rather than state because nothing renders from
  // it: what the reader sees is `pending`, and a timer handle changing must not
  // re-render a list.
  const windows = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  // Every request this hook has sent, counted. Per hook rather than per row so
  // that no two attempts ever share a number — which is what makes dropping a
  // row's entry below safe.
  const sent = useRef(0);

  // The attempt each row is waiting on. An answer whose number is no longer
  // here is no longer the row's news: either a later tap replaced it, or the
  // window it belonged to closed and took the entry with it. `useActivity`
  // keeps one generation for one list; a row here supersedes only itself.
  const waiting = useRef(new Map<string, number>());

  useEffect(() => {
    const running = windows.current;
    // Every timer, cleared. A leaked 60 s timer per toggle is how a Home Screen
    // app that lives for days ends up storming the API from a bench nobody has
    // open — and the invalidate it would run belongs to a tree that is gone.
    return () => {
      for (const timer of running.values()) clearTimeout(timer);
      running.clear();
    };
  }, []);

  /**
   * Open (or reopen) a row's window: the note lives until it closes, and the
   * list is read again when it does. Restarted rather than stacked, because the
   * window belongs to the last thing this client asked for.
   */
  const openWindow = useCallback(
    (id: string) => {
      const running = windows.current;
      const existing = running.get(id);
      if (existing !== undefined) clearTimeout(existing);
      running.set(
        id,
        setTimeout(() => {
          // Hygiene, not behaviour: clearing a handle that has already fired is
          // a no-op, and this only keeps the map to the rows still counting.
          running.delete(id);
          // The row is waiting on no answer now: one still in flight has been
          // outlived by its window and must not rewrite the note.
          waiting.current.delete(id);
          // The note stays up until the read below lands, and is marked with
          // the count it is waiting to see move. The note is always this
          // window's — the same call set one and opened the other — so there is
          // nothing to check before marking it.
          const mark = landedReads(queryClient.getQueryState(TRIGGERS_KEY));
          setPending((current) => ({ ...current, [id]: { ...current[id], closed: mark } }));
          // Whatever the note said, the server now speaks for the row. A
          // disabled bench is invalidated without being run, so this costs a
          // read only if someone is looking — and the note waits for the read
          // the bench runs on the way back in.
          void queryClient.invalidateQueries({ queryKey: TRIGGERS_KEY });
        }, REREAD_MS),
      );
    },
    [queryClient],
  );

  /**
   * Send one fire-and-forget request: note it on its row, open the row's
   * window, and record what came back — on the row, never on the bench. One
   * trigger refusing to be read says nothing about the other twelve.
   *
   * There is deliberately no "already pending" guard: the switch is disabled
   * while a note is up, but if a second request does arrive it is the newer
   * intent, and it takes the row's note and its window with it.
   */
  const queue = useCallback(
    (
      id: string,
      control: PendingKind,
      send: () => Promise<void>,
      accepted?: (at: number) => void,
    ) => {
      const at = Date.now();
      const mine = ++sent.current;
      waiting.current.set(id, mine);
      // Built fresh, never spread over the note it replaces: a second tap after
      // a refusal is a new request that has not failed, and inheriting the old
      // `error` and `status` would put a failure sentence — or the card that
      // says the record cannot be read — under a control that is merely busy.
      setPending((current) => ({ ...current, [id]: { kind: control, at } }));
      openWindow(id);
      void send().then(
        () => {
          // Deliberately not behind the guard below. That the server took this
          // request is a fact about what this client did, and stays true
          // whatever has happened to the row since: dropping it would leave the
          // button reading `Fire` for a fire that was accepted, and the next
          // tap would queue a second one.
          accepted?.(at);
        },
        (error: unknown) => {
          // A later tap has spoken for this row, or the window this attempt
          // belonged to closed and the list is about to speak for it. Either
          // way this answer is no longer the row's news, and a note now would
          // be a client claim standing on nothing (spec §5.2).
          if (waiting.current.get(id) !== mine) return;
          // The guard above is also what makes the spread below safe: a row
          // still waiting on this attempt has not had its note dropped.
          setPending((current) => ({
            ...current,
            [id]: {
              ...current[id],
              error: errorText(error),
              ...(error instanceof ApiError ? { status: error.status } : {}),
            },
          }));
        },
      );
    },
    [openWindow],
  );

  const toggle = useCallback(
    (trigger: Trigger) => {
      const id = trigger.trigger_id;
      // The opposite of what the row is showing, which is the last thing the
      // server said. Never the opposite of a state this client guessed at.
      const wanted = !trigger.enabled;
      queue(id, wanted ? "enabling" : "disabling", () => setTriggerEnabled(id, wanted));
    },
    [queue],
  );

  const fire = useCallback(
    (trigger: Trigger) => {
      const id = trigger.trigger_id;
      // Stamped only once the server has taken it, and with the instant the
      // note already quotes, so the button and the note cannot disagree.
      queue(id, "firing", () => fireTrigger(id), (at) =>
        setFired((current) => {
          // Two fires can be in flight at once and answer out of order; the
          // button reads the most recent acceptance, never an older one that
          // landed late.
          const known = current[id];
          return known !== undefined && known >= at ? current : { ...current, [id]: at };
        }),
      );
    },
    [queue],
  );

  const toggleOpen = useCallback((id: string) => {
    setOpen((current) => (current === id ? null : id));
  }, []);

  const triggers = useMemo(() => query.data ?? [], [query.data]);
  const shown = useMemo(
    () => (kind === "all" ? triggers : triggers.filter((row) => triggerKind(row) === kind)),
    [triggers, kind],
  );

  return {
    kind,
    setKind,
    triggers,
    shown,
    open,
    toggleOpen,
    pending,
    toggle,
    fire,
    fired,
    loading: query.isFetching,
    read: query.dataUpdatedAt !== 0,
    // The read is idle behind a closed bench and a query holds its last error
    // for as long as it is cached, so a bench nobody is looking at complains
    // about nothing.
    error: enabled && query.error ? errorText(query.error) : null,
  };
}
