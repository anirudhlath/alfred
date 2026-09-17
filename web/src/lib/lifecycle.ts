/**
 * Call `fn` whenever the app comes back to the foreground.
 *
 * Two events, because iOS uses both: `visibilitychange` for an app switch, and
 * `pageshow` with `persisted` for a restore out of the back-forward cache, which
 * fires no visibility change at all.
 */
export function onVisible(fn: () => void): () => void {
  const onVisibility = () => {
    if (document.visibilityState === "visible") fn();
  };
  const onPageShow = (event: Event) => {
    if ((event as PageTransitionEvent).persisted) fn();
  };

  document.addEventListener("visibilitychange", onVisibility);
  window.addEventListener("pageshow", onPageShow);

  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    window.removeEventListener("pageshow", onPageShow);
  };
}
