import { useCallback, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ApiError, errorText } from "@/lib/api";
import {
  fetchEpisodic,
  fetchRoutines,
  fetchScratchpad,
  fetchSemantic,
  type EpisodicRow,
  type Routine,
  type Scratchpad,
  type SemanticFile,
} from "@/lib/memory";
import { useOverview } from "@/room/useOverview";

/** The bench's four sub-tabs, in the order it draws them. */
export type MemoryTab = "episodic" | "semantic" | "routines" | "scratchpad";

/**
 * What is known about the embedder. There is no readiness probe anywhere in the
 * API, so the only way to learn it is down is to search and be refused: the pill
 * starts at `unknown` and stays there until a search has answered.
 */
export type ModelState = "unknown" | "ok" | "503";

/**
 * How long a read stays fresh. A trip to Triggers and back inside this window
 * re-uses what is already in hand, and none of these reads is cheap enough to
 * repeat for nothing: a hot browse `SCAN`s a Redis keyspace and `HGETALL`s
 * every key it finds, the semantic read globs two directories off disk, and
 * memory changes at consolidation speed rather than at chat speed.
 */
const STALE_MS = 30_000;

/**
 * The Librarian's nightly pass, for the scratchpad's stat card. Both stamps are
 * ISO strings the server sends, and both can be absent: a house that has never
 * consolidated has no last run, and a Librarian that is not scheduled has no
 * next one. Null when the overview has not answered — not a pair of nulls,
 * which would read as "scheduled: never".
 */
export interface Consolidation {
  last: string | null;
  next: string | null;
}

export interface Memory {
  tab: MemoryTab;
  setTab: (tab: MemoryTab) => void;
  /** What the search field holds. Typing this does not search. */
  query: string;
  setQuery: (query: string) => void;
  /**
   * The query the rows on screen actually answer — what was last submitted,
   * which is not what the field holds the moment the reader types again. The
   * empty state quotes this, so it cannot claim the server was asked for a word
   * it has never seen. `""` while browsing.
   */
  submitted: string;
  /** The form's onSubmit. An empty query submits as a browse. */
  submit: () => void;
  /** A submitted query is in flight. A browse is not a search. */
  searching: boolean;
  /**
   * The rows on screen are a search's matches rather than a browse — which is
   * what tells the two empty states apart ("nothing scored above the
   * threshold" against "no episodic memories yet"). Read from the answer and
   * never from the field: a search still in flight, or one that was refused, is
   * showing the browse and must not claim otherwise.
   */
  searched: boolean;
  /** What the list shows: the search's matches, or the browse under them. */
  rows: EpisodicRow[];
  model: ModelState;
  files: SemanticFile[];
  routines: Routine[];
  scratchpad: Scratchpad | null;
  /** When the Librarian last ran and when it runs next, or null until the overview has answered. */
  consolidation: Consolidation | null;
  /** The one expanded routine's name. */
  openRoutine: string | null;
  toggleRoutine: (name: string) => void;
  /** A read is in flight. */
  loading: boolean;
  /** Why the showing tab's read failed, if it did. Null while the bench is not showing. */
  error: string | null;
}

/**
 * The Memory bench's one hook: four reads, one of them submit-driven, and an
 * honest pill for the embedder.
 *
 * `enabled` is `bench === "memory"`. It gates every query, because a bench
 * nobody is looking at must not scan a Redis keyspace or glob a directory on
 * the server — but the hook lives in `WorkshopPanel` rather than in the bench,
 * so the sub-tab, the query and the open routine survive a trip to Triggers.
 */
export function useMemory(enabled: boolean): Memory {
  const [tab, setTab] = useState<MemoryTab>("episodic");
  const [query, setQuery] = useState("");
  // What the field holds and what the server was asked are two different
  // things: searching on every keystroke would be a recall() per letter, each
  // one embedding the query on the GPU. "" is a browse.
  const [submitted, setSubmitted] = useState("");
  const [model, setModel] = useState<ModelState>("unknown");
  const [openRoutine, setOpenRoutine] = useState<string | null>(null);
  const showing = enabled && tab === "episodic";

  // Browse and search are two queries rather than one keyed on the query
  // string, so that a refused search still has the browse under it to show. A
  // single key would leave the list empty on a 503 — react-query holds `data`
  // per key, and a failed key has none of its own; `placeholderData` does not
  // cover that either, since the observer reaches for a placeholder only while
  // a query is pending, never once it has errored. A failed search is no reason
  // to take the last true thing we were told off the screen (spec §5.2).
  const browseQuery = useQuery({
    queryKey: ["memory", "episodic", "browse"],
    queryFn: () => fetchEpisodic(""),
    enabled: showing,
    staleTime: STALE_MS,
  });

  const searchQuery = useQuery({
    queryKey: ["memory", "episodic", "search", submitted],
    // The pill is set here, where the answer arrives, in the idiom `useOverview`
    // already uses for `markTrue`: the embedder has no readiness probe, so a
    // search answering is the only evidence there is. Unlike `markTrue`, which
    // writes module state, this writes *React* state from outside render —
    // legitimate in a queryFn, which runs from the fetch rather than from a
    // render or an effect, but it lands a tick after the rows it explains, so
    // every assertion about the pill has to wait for it. Do not copy the idiom
    // into a component body, where it would be a render-phase update.
    //
    // Only a 503 is the model: a 401 is a gate and a 500 is the store behind
    // it, and reading either as a dead embedder would be inventing a diagnosis.
    queryFn: async () => {
      try {
        const found = await fetchEpisodic(submitted);
        setModel("ok");
        return found;
      } catch (error) {
        if (error instanceof ApiError && error.status === 503) setModel("503");
        throw error;
      }
    },
    enabled: showing && submitted !== "",
    // Holds the matches already on screen while the next search is in flight,
    // so a second search does not flash the browse between the two answers.
    // Inert once a query has errored — placeholders are for pending queries —
    // which is exactly what leaves a refused search falling back to the browse.
    placeholderData: keepPreviousData,
    staleTime: STALE_MS,
  });

  const semanticQuery = useQuery({
    queryKey: ["memory", "semantic"],
    queryFn: fetchSemantic,
    enabled: enabled && tab === "semantic",
    staleTime: STALE_MS,
  });

  const routinesQuery = useQuery({
    queryKey: ["memory", "routines"],
    queryFn: fetchRoutines,
    enabled: enabled && tab === "routines",
    staleTime: STALE_MS,
  });

  const scratchpadQuery = useQuery({
    queryKey: ["memory", "scratchpad"],
    queryFn: fetchScratchpad,
    enabled: enabled && tab === "scratchpad",
    staleTime: STALE_MS,
  });

  // The scratchpad's second stat card is the Librarian's schedule, which lives
  // on the overview rather than on `/memory/scratchpad` (plan deviation 15).
  // Disabled on purpose: this is a read of what the key already holds — the
  // Room and the Workshop's own header are polling it — and a stamp on a stat
  // card is not worth a request of its own. A disabled observer still re-renders
  // when their poll lands.
  const librarian = useOverview(false).data?.librarian;

  // One source for both: the rows are the search's matches when it has any, and
  // `searched` is that same fact. The `submitted` guard is not belt and braces
  // — a disabled query is *pending*, so clearing the field back to a browse
  // would otherwise let `keepPreviousData` hand back the matches for words that
  // are no longer in the box.
  const matches = submitted === "" ? undefined : searchQuery.data;
  const searched = matches !== undefined;
  const rows = matches ?? browseQuery.data ?? [];

  // Lifted out of the callback below only to keep its dependency list honest:
  // react-query's `refetch` is stable per observer, while depending on the
  // whole query result would rebuild `submit` on every fetch-state change.
  const refetchBrowse = browseQuery.refetch;
  const refetchSearch = searchQuery.refetch;
  const submit = useCallback(() => {
    // Whitespace is not a question worth embedding; it submits as a browse.
    const next = query.trim();
    if (next !== submitted) {
      setSubmitted(next);
      return;
    }
    // The same words a second time is a retry — the key does not change, and a
    // settled query does not re-run for a state change it cannot see.
    void (next === "" ? refetchBrowse() : refetchSearch());
  }, [query, submitted, refetchBrowse, refetchSearch]);

  const toggleRoutine = useCallback((name: string) => {
    setOpenRoutine((current) => (current === name ? null : name));
  }, []);

  // The showing tab's failure, and only it: the other tabs are disabled, and a
  // query holds its last error for as long as it is cached, so reading all of
  // them would carry one tab's old trouble onto the one in front of the reader.
  // A bench that is not showing reports nothing at all — with every read idle,
  // a cached error is a complaint about a screen nobody is looking at.
  const failures = {
    episodic: searchQuery.error ?? browseQuery.error,
    semantic: semanticQuery.error,
    routines: routinesQuery.error,
    scratchpad: scratchpadQuery.error,
  } satisfies Record<MemoryTab, Error | null>;
  const failure = enabled ? failures[tab] : null;

  return {
    tab,
    setTab,
    query,
    setQuery,
    submitted,
    submit,
    searching: searchQuery.isFetching,
    searched,
    rows,
    model,
    files: semanticQuery.data ?? [],
    routines: routinesQuery.data ?? [],
    scratchpad: scratchpadQuery.data ?? null,
    consolidation: librarian ? { last: librarian.last_run_at, next: librarian.next_run_at } : null,
    openRoutine,
    toggleRoutine,
    loading:
      browseQuery.isFetching ||
      searchQuery.isFetching ||
      semanticQuery.isFetching ||
      routinesQuery.isFetching ||
      scratchpadQuery.isFetching,
    error: failure === null ? null : errorText(failure),
  };
}
