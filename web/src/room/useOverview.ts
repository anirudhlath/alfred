import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { SESSION_IDLE_MS } from "@/lib/history";
import type { Overview } from "@/lib/types";
import { markTrue } from "@/shell/ConnectionProvider";

/**
 * The overview's cache key, named here because this is the query that defines
 * it. Deliberately *not* exported for `REHYDRATE_KEYS` to import: this module
 * imports `markTrue` from `ConnectionProvider`, so the import would close a
 * cycle whose evaluation order decides whether `REHYDRATE_KEYS` reads the key
 * or a temporal-dead-zone `undefined`. `ConnectionProvider` spells the prefix
 * out instead, and says so where it does. Every re-read of this query in the
 * app goes through the `refetch` this hook returns, which needs no key at all.
 */
const OVERVIEW_KEY = ["overview"] as const;

/**
 * The Room's vitals. Polled rather than pushed: the overview aggregates Redis
 * reads that no stream announces. A successful read is also proof the house is
 * reachable, so it stamps last-true.
 *
 * `enabled` is for the callers that want what this key already holds without
 * asking for it again: a disabled observer never fetches and still re-renders
 * when whoever *is* polling gets an answer. The Memory bench's scratchpad reads
 * the Librarian's schedule that way — the Room and the Workshop header are
 * already on this key, and a stamp on a stat card is not worth a read of its
 * own (plan deviation 15).
 */
export function useOverview(enabled = true) {
  return useQuery<Overview>({
    queryKey: OVERVIEW_KEY,
    enabled,
    queryFn: async () => {
      const overview = await api<Overview>("/api/admin/overview");
      markTrue();
      return overview;
    },
    refetchInterval: 30_000,
    // Two observers on this key do not double the polls, but `staleTime` is not
    // what stops them: `refetchInterval` does not consult staleness. What
    // re-syncs them is react-query restarting every observer's interval on each
    // query update (`onQueryUpdate` → `#updateTimers`), so a second observer
    // joining mid-cycle lands on the first one's cadence rather than beside it.
    //
    // 25 s is here for the *fetches*, not the timers: under the interval, so
    // the shared 30 s cadence is unchanged, and above the trip a reader makes
    // to the Workshop and back, which would otherwise be a read of its own.
    // Foreground rehydration still works either way — `invalidateQueries`
    // refetches an *active* query whatever its staleness (ConnectionProvider,
    // REHYDRATE_KEYS).
    staleTime: 25_000,
  });
}

/**
 * True when every catalogued stream is empty — nothing has ever happened here.
 *
 * An *absent* streams map is a different thing entirely (Redis is down), and must
 * not read as a first run. Shared by the status line and the headline so the two
 * can never disagree about which morning this is.
 */
export function isFirstRun(overview: Overview | undefined): boolean {
  const streams = overview?.streams ?? {};
  return (
    Object.keys(streams).length > 0 && Object.values(streams).every((stream) => stream.length === 0)
  );
}

/**
 * The server's session idle timeout in ms, or the client's default until the
 * overview has answered (or if it reports nonsense — a zero would window
 * everything away, an `Infinity` would never let the window break or the socket
 * rotate). The house always sends `session`; the guard is for a cached shell
 * meeting a server from before it did, not for the current contract.
 */
export function sessionIdleMs(overview: Overview | undefined): number {
  // `Number.isFinite` narrows nothing, so the read defaults rather than the
  // guard testing for `undefined` — absent and nonsense take the same fallback.
  const minutes = overview?.session?.idle_minutes ?? Number.NaN;
  return Number.isFinite(minutes) && minutes > 0 ? minutes * 60_000 : SESSION_IDLE_MS;
}
