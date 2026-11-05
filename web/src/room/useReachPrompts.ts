import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TimelineItem } from "@/lib/history";
import { REACH_ACTS_KEY, reachActRow, readReachActs } from "@/lib/reach-local";

/**
 * The iOS dialog's answers as the Room's rows. Storage is read once, when the cache
 * entry is made, and after that the entry is the list, which `usePush` writes the
 * moment the dialog answers. Disabled, so a sign-in's `invalidateQueries()` has
 * nothing to refetch: a re-read would take back a row the store refused to keep.
 * Once the cache has let the entry go, with the Room unmounted, such a row is gone.
 */
export function useReachActRows(): TimelineItem[] {
  const { data } = useQuery({
    queryKey: REACH_ACTS_KEY,
    // Never called while the query is disabled, and nothing here refetches it.
    // It is here because react-query logs an error on every render without one.
    queryFn: readReachActs,
    initialData: readReachActs,
    enabled: false,
  });
  return useMemo(() => data.map(reachActRow), [data]);
}
