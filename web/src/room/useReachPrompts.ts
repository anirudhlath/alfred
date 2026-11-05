import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ReachGate } from "@/gates/ReachGates";
import { onIPhone } from "@/lib/device";
import type { TimelineItem } from "@/lib/history";
import { fetchPublicKey, permission, reachContext, type ReachContext } from "@/lib/push";
import { gateAnswered, REACH_ACTS_KEY, reachActRow, readReachActs } from "@/lib/reach-local";

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

/** The key read's own cache entry. The sync reads the key for itself (`syncSubscription`). */
export const PUSH_KEY_QUERY_KEY = ["push", "public-key"] as const;

/**
 * Whether the house has a Web Push key, asked only once it is `wanted`. A prompt
 * promising a knock needs one to keep that promise. `answered` covers a read that
 * failed, with `has` false: there is no retry, since the next launch reads anew.
 */
export function useHouseKey(wanted: boolean): { answered: boolean; has: boolean } {
  const { data, isFetched } = useQuery({
    queryKey: PUSH_KEY_QUERY_KEY,
    queryFn: fetchPublicKey,
    enabled: wanted,
    retry: false,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });
  return { answered: isFetched, has: typeof data === "string" };
}

export interface ReachPrompts {
  /** The gate the Room has up: for now, only the one that opens itself. */
  gate: ReachGate | null;
  closeGate: () => void;
}

/**
 * What the Room raises about notifications without being asked: the standalone
 * gate, once on launch, in the installed app on an iPhone (the spec's D9) with the
 * permission still `default`, a button not yet tapped and a key on the house.
 * A launch with neither button tapped writes nothing, so the next one shows it.
 */
export function useReachPrompts(): ReachPrompts {
  // Read once, each of them. A launch is judged as it began: what its own gate
  // goes on to change is not a reason to judge it again.
  const [iPhone] = useState(onIPhone);
  const [context] = useState<ReachContext>(reachContext);
  const [eligible] = useState(
    () => iPhone && context === "capable" && permission() === "default" && !gateAnswered(),
  );
  const key = useHouseKey(eligible);
  const [gate, setGate] = useState<ReachGate | null>(null);

  // Set on the key's first answer, whatever it said. It is what stops a later
  // re-read reopening the gate: a sign-in's `invalidateQueries()` reaches the key,
  // and this, with the gate closed, is what holds a tap the store refused to keep.
  // Adjusted during render, as `usePresence` adjusts its own.
  const [selfOpened, setSelfOpened] = useState(false);
  if (eligible && key.answered && !selfOpened) {
    setSelfOpened(true);
    if (key.has) setGate("standalone");
  }

  const closeGate = useCallback(() => setGate(null), []);
  return { gate, closeGate };
}
