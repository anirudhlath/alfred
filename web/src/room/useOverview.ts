import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { evs } from "@/lib/format";
import { SESSION_IDLE_MS } from "@/lib/history";
import type { Overview } from "@/lib/types";
import { markTrue } from "@/shell/ConnectionProvider";

/**
 * The Room's vitals. Polled rather than pushed: the overview aggregates Redis
 * reads that no stream announces. A successful read is also proof the house is
 * reachable, so it stamps last-true.
 */
export function useOverview() {
  return useQuery<Overview>({
    queryKey: ["overview"],
    queryFn: async () => {
      const overview = await api<Overview>("/api/admin/overview");
      markTrue();
      return overview;
    },
    refetchInterval: 30_000,
    // The Workshop's header watches this query too, and a second observer with
    // no staleTime starts its own interval offset from the Room's — roughly
    // double the polls for as long as the layer is up. 25 s is under the
    // interval, so the shared 30 s cadence is unchanged, and foreground
    // rehydration still works: `invalidateQueries` refetches an *active* query
    // whatever its staleness (ConnectionProvider, REHYDRATE_KEYS).
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
 * `2.1 ev/s`, or `— ev/s` when the map is absent or empty. An empty `streams`
 * is Redis down (see `isFirstRun`), and `evs({})` is a bare `0` — the string
 * format.ts reserves for a house that really is silent. A first run keeps its
 * keys, each at length 0, and still reads `0 ev/s`.
 *
 * Shared by the Room's status line and the Workshop's so the two cannot drift
 * into telling different stories about the same map.
 */
export function rateText(overview: Overview | undefined): string {
  const streams = overview?.streams;
  return streams && Object.keys(streams).length > 0 ? `${evs(streams)} ev/s` : "— ev/s";
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
