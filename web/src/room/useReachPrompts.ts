import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import type { ReachGate } from "@/gates/ReachGates";
import type { TrackedAction } from "@/lib/actions";
import { onIPhone } from "@/lib/device";
import type { TimelineItem } from "@/lib/history";
import { fetchPublicKey, permission, reachContext, type ReachContext } from "@/lib/push";
import {
  gateAnswered,
  REACH_ACTS_KEY,
  reachActRow,
  readAnsweredDoors,
  readReachActs,
  writeAnsweredDoors,
} from "@/lib/reach-local";

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
 * failed, with `has` false: there is no retry, since the next launch reads anew, as
 * does the next Door once a tab has stopped wanting it.
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
  /** The prompt row, of kind `reach`, while it has something true to say. */
  row: TimelineItem | null;
  /** The row's `Not now`: answers every Door waiting at that moment. */
  notNow: () => void;
  /** The row's `Show me how`: opens the install gate. */
  showMe: () => void;
  /** The gate the Room has up: the one the row opened, or the one that opens itself. */
  gate: ReachGate | null;
  closeGate: () => void;
}

/** The row's time. An unreadable stamp has no place in the thread's sort or its day dividers. */
function doorTime({ action }: TrackedAction, now: number): string {
  return Number.isNaN(Date.parse(action.timestamp))
    ? new Date(now).toISOString()
    : action.timestamp;
}

/**
 * What the Room raises about notifications without being asked, on an iPhone only
 * (the spec's D9).
 *
 * In a tab, the prompt row: while a Door waits that `Not now` has not answered and
 * the house has a key, at the time of the first such Door. It is derived from those
 * facts, so it leaves with the last of them and a reload brings it back. `Not now`
 * holds until a Door arrives that was not waiting when it was tapped (the spec's D11).
 *
 * In the installed app, the standalone gate, once on launch, with the permission
 * still `default`, a button not yet tapped and a key on the house. A launch with
 * neither button tapped writes nothing, so the next one shows it.
 *
 * `pending` is oldest first, as `DoorProvider` keeps it, and `now` is its clock.
 */
export function useReachPrompts(pending: TrackedAction[], now: number): ReachPrompts {
  // Read once, each of them: this is what decides whether the key is wanted, and
  // a tap on the launch's own gate is no reason to take that back.
  const [iPhone] = useState(onIPhone);
  const [context] = useState<ReachContext>(reachContext);
  const [eligible] = useState(
    () => iPhone && context === "capable" && permission() === "default" && !gateAnswered(),
  );
  const [gate, setGate] = useState<ReachGate | null>(null);
  // Held here as well as stored, so a tap the store refused still holds for this run.
  const [answered, setAnswered] = useState(readAnsweredDoors);
  const waiting = pending.filter(({ action }) => !answered.includes(action.request_id));
  const offering = iPhone && context === "tab" && waiting.length > 0;
  const key = useHouseKey(eligible || offering);

  // A string, so for a readable stamp a Door tick neither re-creates the row nor re-sorts
  // the thread for it. An unreadable stamp follows the clock, so there it does both.
  const at = offering && key.has ? doorTime(waiting[0], now) : null;
  const row = useMemo<TimelineItem | null>(
    () => (at === null ? null : { kind: "reach", id: "reach:prompt", at }),
    [at],
  );

  // Through a ref, as `useRoom` reads its queue: `pending` is a new list on every
  // Door tick, and `notNow` must not be a new function with it.
  const pendingRef = useRef(pending);
  useEffect(() => {
    pendingRef.current = pending;
  }, [pending]);

  // Every Door then waiting, replacing the old list, so it cannot grow.
  const notNow = useCallback(() => {
    const ids = pendingRef.current.map(({ action }) => action.request_id);
    setAnswered(ids);
    writeAnsweredDoors(ids);
  }, []);
  const showMe = useCallback(() => setGate("install"), []);

  // Set on the key's first answer, whatever it said. It is what stops a later
  // re-read reopening the gate: a sign-in's `invalidateQueries()` reaches the key.
  // With the gate closed, it is also all that holds a tap the store refused to
  // keep, and only as long as this Room is mounted: a new mount reads the store
  // anew. It holds nothing from before the key's answer either, so on that store a
  // `Not yet` or a dismissed dialog through Reach during a slow read still opens the
  // gate once (the spec's section 4 allows both).
  // Adjusted during render, as `usePresence` adjusts its own.
  const [selfOpened, setSelfOpened] = useState(false);
  if (eligible && key.answered && !selfOpened) {
    setSelfOpened(true);
    // Both again: a slow key read leaves time to answer through Reach's own gate.
    if (key.has && permission() === "default" && !gateAnswered()) setGate("standalone");
  }

  const closeGate = useCallback(() => setGate(null), []);
  return { row, notNow, showMe, gate, closeGate };
}
