import { useSyncExternalStore } from "react";

function subscribe(onChange: () => void): () => void {
  window.addEventListener("online", onChange);
  window.addEventListener("offline", onChange);
  return () => {
    window.removeEventListener("online", onChange);
    window.removeEventListener("offline", onChange);
  };
}

const onLine = (): boolean => navigator.onLine;

/**
 * Whether this phone believes it has a network.
 *
 * Used for exactly one thing: explaining a read that has **not landed**. Never
 * to claim the house is reachable — `navigator.onLine` is true for a phone on a
 * café's Wi-Fi with no route to anything — and never to gate a request.
 *
 * It is not react-query's idea of online, and the two disagree exactly when it
 * matters. query-core's `onlineManager` starts out online and changes only on
 * the window's `online` and `offline` events; it never reads this flag. A launch
 * that begins with no network fires neither, so react-query believes it is
 * online and runs every read, which fails — nothing is paused. Only this flag
 * knows the phone was offline from the start. Handing it to `onlineManager`
 * instead would make it gate every request in the app, which is the one thing
 * it must not do: a phone that wrongly reports offline would ask nothing at all
 * until an `online` event that might never come.
 *
 * `useSyncExternalStore` rather than state set from the events, because it also
 * reads the flag when it subscribes: an event fired between the first render and
 * the subscription — a phone handing over from Wi-Fi to cellular as the app
 * opens — reaches no listener, and would otherwise be lost until the next one.
 */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, onLine);
}
