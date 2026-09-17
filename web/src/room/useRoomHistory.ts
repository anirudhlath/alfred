import { useQuery } from "@tanstack/react-query";
import { fetchRoomHistory, type RoomHistory } from "@/lib/history";

/**
 * The thread as it stood when the app opened. Read once, then only again when
 * the app comes back to the foreground — `ConnectionProvider` invalidates
 * `["room-history"]` on `visibilitychange`, because the telemetry socket starts
 * at `$` and replays nothing (constraint §4.10).
 */
export function useRoomHistory() {
  return useQuery<RoomHistory>({
    queryKey: ["room-history"],
    queryFn: fetchRoomHistory,
    staleTime: 30_000,
  });
}
