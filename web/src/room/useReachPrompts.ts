import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import type { TimelineItem } from "@/lib/history";
import { REACH_ACTS_KEY, reachActRow, readReachActs } from "@/lib/reach-local";

/**
 * The iOS dialog's answers as the Room's rows. Read from storage once, and after
 * that from the cache, which `usePush` writes the moment the dialog answers: the
 * cache is what shows a row this run when the store refused to keep it.
 */
export function useReachActRows(): TimelineItem[] {
  const { data } = useQuery({
    queryKey: REACH_ACTS_KEY,
    queryFn: readReachActs,
    initialData: readReachActs,
    staleTime: Infinity,
  });
  return useMemo(() => data.map(reachActRow), [data]);
}
