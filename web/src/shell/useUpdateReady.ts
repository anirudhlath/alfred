import { useEffect, useState } from "react";
import { onUpdateReady } from "@/lib/sw";

/**
 * Whether a changed `/sw.js` has installed behind the running worker and is
 * waiting for permission to take over. Not whether a deploy has happened: most
 * leave `/sw.js` as it was and install nothing (`onUpdateReady`).
 *
 * State and an effect rather than `useSyncExternalStore`, because `lib/sw.ts`
 * offers a subscription that answers at once, not a snapshot to read during
 * render. The first render therefore says no even with a worker already
 * waiting, and the answer arrives a commit later — so `UpdateNote`'s live
 * region is in the page, empty, before its words are.
 */
export function useUpdateReady(): boolean {
  const [ready, setReady] = useState(false);
  useEffect(() => onUpdateReady(setReady), []);
  return ready;
}
