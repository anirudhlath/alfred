import {
  useEffect,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type RefObject,
} from "react";

/** The handoff's reduce-motion substitute for every rise. */
const REDUCED_MOTION_MS = 200;

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION_QUERY).matches;
}

function subscribeReducedMotion(onChange: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => {};
  const query = window.matchMedia(REDUCED_MOTION_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

/**
 * `prefersReducedMotion()`, kept current. The setting can change while the app
 * is open (Settings → Accessibility → Motion), and anything that read it once
 * at mount — a canvas loop, say — would keep animating.
 */
export function useReducedMotion(): boolean {
  return useSyncExternalStore(subscribeReducedMotion, prefersReducedMotion);
}

export interface Presence {
  mounted: boolean;
  leaving: boolean;
}

/**
 * Keep a surface mounted for the length of its leave animation.
 *
 * A timer, not `animationend`: jsdom fires no animation events, and a missed
 * event would strand a full-screen layer over the app forever. The duration is
 * the caller's, except under reduce-motion where every leave is 200 ms.
 */
export function usePresence(open: boolean, durationMs: number): Presence {
  const [mounted, setMounted] = useState(open);
  const [leaving, setLeaving] = useState(false);
  const [wasOpen, setWasOpen] = useState(open);

  // State adjusted during render, the way react.dev documents for "storing
  // information from previous renders": an opening layer is mounted on this
  // very pass, and a closing one starts its leave, with no effect in between.
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setMounted(true);
      setLeaving(false);
    } else if (mounted) {
      setLeaving(true);
    }
  }

  useEffect(() => {
    if (!leaving) return;
    const ms = prefersReducedMotion() ? REDUCED_MOTION_MS : durationMs;
    const timer = setTimeout(() => {
      setMounted(false);
      setLeaving(false);
    }, ms);
    // Re-opening mid-leave flips `leaving` back, which clears this.
    return () => clearTimeout(timer);
  }, [leaving, durationMs]);

  return { mounted, leaving };
}

/**
 * The last non-null value, held through the leave.
 *
 * A surface is still on screen for its leave animation after the thing it was
 * showing has gone: `close()` clears the Door's tracked action and the Why
 * sheet's anchor the moment they are dismissed, and an empty panel sliding
 * away is worse than the one just dismissed sliding away. `usePresence` above
 * keeps the surface mounted; this keeps its contents.
 *
 * Adjusted during render, the way `usePresence` is and for the same reason:
 * the copy must never lag the prop by a frame, which an effect would.
 * `null` never overwrites — that is the whole point — so the caller gets the
 * last real value for as long as it has none of its own.
 */
export function useLatched<T>(value: T | null): T | null {
  const [shown, setShown] = useState(value);
  if (value !== null && value !== shown) setShown(value);
  return shown;
}

/** The modal surfaces that are up, bottom to top. Everything under the top one is inert. */
const surfaces: HTMLElement[] = [];

/**
 * While a modal surface is up, everything behind it is inert and focus lives
 * in the surface — Full Keyboard Access and VoiceOver must not wander into a
 * room they cannot see. That includes a surface under another one: a gate over
 * an open sheet makes the sheet inert too, and gives it back when it leaves.
 * Focus goes back where it was once the surface has left, unless that place
 * is now behind another surface.
 *
 * `active` is the presence's `mounted`, so the surface stays inert-backed for
 * its leave animation too, and the panel ref is set by the time this runs.
 */
export function useModalFocus(active: boolean, panel: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const node = panel.current;
    if (!active || !node) return;
    const app = document.getElementById("root");
    const previous = document.activeElement;
    app?.setAttribute("inert", "");
    surfaces.at(-1)?.setAttribute("inert", "");
    surfaces.push(node);
    node.focus({ preventScroll: true });
    return () => {
      const index = surfaces.indexOf(node);
      if (index >= 0) surfaces.splice(index, 1);
      const top = surfaces.at(-1);
      if (top) top.removeAttribute("inert");
      else app?.removeAttribute("inert");
      if (
        previous instanceof HTMLElement &&
        previous.isConnected &&
        previous.closest("[inert]") === null
      ) {
        previous.focus({ preventScroll: true });
      }
    };
  }, [active, panel]);
}

type RiseStyle = CSSProperties & { "--layer-duration": string };

/** The theme's surface colours and the duration `.rise-in`/`.rise-out` read. */
export function riseStyle(durationMs: number): RiseStyle {
  return { background: "var(--bg)", color: "var(--fg)", "--layer-duration": `${durationMs}ms` };
}

export function riseClass(leaving: boolean): string {
  return leaving ? "rise-out" : "rise-in";
}
