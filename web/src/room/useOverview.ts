import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
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
 * everything away). The house always sends `session`; the guard is for a cached
 * shell meeting a server from before it did, not for the current contract.
 */
export function sessionIdleMs(overview: Overview | undefined): number {
  const minutes = overview?.session?.idle_minutes;
  return typeof minutes === "number" && minutes > 0 ? minutes * 60_000 : SESSION_IDLE_MS;
}
