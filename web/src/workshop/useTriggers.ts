import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
 * How long a queued change stays unknown. `POST …/enabled` answers
 * `{"status":"queued","effective_within_seconds":60}`: the route publishes to
 * the actions stream and the triggers process applies the change inside its own
 * 60 s cache window. Nothing tells this client when that happened — so the row
 * waits out the window with a note on it, and then the list is read again.
 */
export const REREAD_MS = 60_000;

/**
 * How long a read stays fresh. Well under the window above, so the re-read that
 * closes it always goes to the server, and long enough that a trip to Memory
 * and back re-uses what is already in hand.
 */
const STALE_MS = 30_000;

/** What the chips filter by: a kind, or every kind at once. */
export type KindFilter = "all" | TriggerKind;

/** Which control is waiting, in the present tense the row prints. */
export type PendingKind = "enabling" | "disabling" | "firing";

/**
 * A request this client has sent and cannot yet see the effect of. One per row,
 * living exactly `REREAD_MS` — a client claim must not outlive the evidence for
 * it (spec §5.2), and after the window the list itself is the evidence.
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
  /** Rows with a request in flight or inside its window, by `trigger_id`. */
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
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [fired, setFired] = useState<Record<string, number>>({});

  // No `refetchInterval`: a trigger list changes when a person or the LLM
  // changes it, never on its own, and the one moment this client has reason to
  // doubt it is the window it opened itself.
  const query = useQuery({
    queryKey: ["triggers"],
    queryFn: fetchTriggers,
    enabled,
    staleTime: STALE_MS,
  });

  // One window per row. A `ref` rather than state because nothing renders from
  // it: what the reader sees is `pending`, and a timer handle changing must not
  // re-render a list.
  const windows = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  // Which attempt a row's note belongs to, so an answer that arrives after a
  // later tap — or after the window closed — can tell it is no longer the
  // row's news. Same idiom as `useActivity`'s read generation.
  const attempts = useRef(new Map<string, number>());

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
          running.delete(id);
          setPending((current) => {
            if (!(id in current)) return current;
            const next = { ...current };
            delete next[id];
            return next;
          });
          // Whatever the note said, the server now speaks for the row. A
          // disabled bench is invalidated without being run, so this costs a
          // read only if someone is looking.
          void queryClient.invalidateQueries({ queryKey: ["triggers"] });
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
    (id: string, kind: PendingKind, send: () => Promise<void>, accepted?: (at: number) => void) => {
      const at = Date.now();
      const mine = (attempts.current.get(id) ?? 0) + 1;
      attempts.current.set(id, mine);
      setPending((current) => ({ ...current, [id]: { kind, at } }));
      openWindow(id);
      void send().then(
        () => {
          if (attempts.current.get(id) !== mine) return;
          accepted?.(at);
        },
        (error: unknown) => {
          if (attempts.current.get(id) !== mine) return;
          setPending((current) => {
            const note = current[id];
            // The window closed while this was in flight: the row has gone back
            // to what the server says, and a failure note now would reopen a
            // claim nothing is holding.
            if (note === undefined) return current;
            return {
              ...current,
              [id]: {
                ...note,
                error: errorText(error),
                ...(error instanceof ApiError ? { status: error.status } : {}),
              },
            };
          });
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
        setFired((current) => ({ ...current, [id]: at })),
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
    // The read is idle behind a closed bench and a query holds its last error
    // for as long as it is cached, so a bench nobody is looking at complains
    // about nothing.
    error: enabled && query.error ? errorText(query.error) : null,
  };
}
