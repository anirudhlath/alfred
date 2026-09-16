import { useCallback, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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
 * re-uses what is already in hand: memory changes at consolidation speed, and a
 * hot browse scans a Redis keyspace to answer.
 */
const STALE_MS = 30_000;

export interface Memory {
  tab: MemoryTab;
  setTab: (tab: MemoryTab) => void;
  /** What the search field holds. Typing this does not search. */
  query: string;
  setQuery: (query: string) => void;
  /** The form's onSubmit. An empty query submits as a browse. */
  submit: () => void;
  /** A submitted query is in flight. A browse is not a search. */
  searching: boolean;
  /**
   * The rows answer a query rather than a browse — which is what tells the two
   * empty states apart ("nothing scored above the threshold" against "no
   * episodic memories yet").
   */
  searched: boolean;
  /** What the list shows: the search's matches, or the browse under them. */
  rows: EpisodicRow[];
  model: ModelState;
  files: SemanticFile[];
  routines: Routine[];
  scratchpad: Scratchpad | null;
  /** The one expanded routine's name. */
  openRoutine: string | null;
  toggleRoutine: (name: string) => void;
  /** A read is in flight. */
  loading: boolean;
  /** Why the showing tab's read failed, if it did. */
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
  // cover it either, since the observer reaches for a placeholder only while a
  // query is pending, never once it has errored. A failed search is no reason
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
    // search answering is the only evidence there is. Only a 503 is the model —
    // a 401 is a gate and a 500 is the store behind it, and reading either as a
    // dead embedder would be inventing the diagnosis.
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

  const searched = submitted !== "";
  const rows = (searched ? searchQuery.data : undefined) ?? browseQuery.data ?? [];

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
  const failure =
    tab === "episodic"
      ? (searchQuery.error ?? browseQuery.error)
      : tab === "semantic"
        ? semanticQuery.error
        : tab === "routines"
          ? routinesQuery.error
          : scratchpadQuery.error;

  return {
    tab,
    setTab,
    query,
    setQuery,
    submit,
    searching: searchQuery.isFetching,
    searched,
    rows,
    model,
    files: semanticQuery.data ?? [],
    routines: routinesQuery.data ?? [],
    scratchpad: scratchpadQuery.data ?? null,
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
