import {
  useEffect,
  useRef,
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
 * Whether any modal surface is up, for something about to raise one of its own
 * unasked (`useReachPrompts`). One in its leave counts: `useModalFocus` holds it
 * until `mounted` goes, and it is still on screen with the app inert behind it. One
 * joins in a passive effect of its own, so a render or a layout effect above it can
 * read this before it has.
 */
export function anySurfaceUp(): boolean {
  return surfaces.length > 0;
}

/**
 * What a detail's focus carries from one mode to the next, when crossing a breakpoint
 * remounts it in another (`Sheet`). One per detail, held above its modes.
 */
export interface Handoff {
  /** Where focus was at the press that opened or last re-pointed the detail. */
  opener: Element | null;
  /** Whether a mode has the detail up, so a mode mounting with this set is a crossing. */
  up: boolean;
  /**
   * The detail's `open` as of this commit, set in a layout effect so a mode's passive
   * cleanup can tell a crossing, which leaves it true, from a close.
   */
  open: boolean;
}

/** Focus back to where it was, unless that place has gone or is behind a surface now. */
function restoreFocus(previous: Element | null): void {
  if (
    previous instanceof HTMLElement &&
    previous.isConnected &&
    previous.closest("[inert]") === null
  ) {
    previous.focus({ preventScroll: true });
  }
}

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
 *
 * A detail's `handoffRef` makes a breakpoint's remount no open and no close: the modal
 * takes the opener the press captured, and leaves giving it back to the mode after it.
 * Focus still moves in, since everything behind the modal is inert.
 */
export function useModalFocus(
  active: boolean,
  panel: RefObject<HTMLElement | null>,
  handoffRef?: RefObject<Handoff>,
): void {
  useEffect(() => {
    const node = panel.current;
    if (!active || !node) return;
    const app = document.getElementById("root");
    const state = handoffRef?.current;
    const previous = state?.up ? state.opener : document.activeElement;
    if (handoffRef) {
      handoffRef.current.up = true;
      handoffRef.current.opener = previous;
    }
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
      if (state?.open) return;
      if (state) state.up = false;
      restoreFocus(previous);
    };
  }, [active, panel, handoffRef]);
}

/**
 * Focus around a surface that sits beside the app rather than over it: nothing
 * goes inert, and the surface joins no stack.
 *
 * With `take` it covers the reader's place, so focus moves in on open. Without, it
 * is beside their place and focus stays there. Either way it comes back on close
 * only from `<body>`, which is where a focused element's removal drops it: this
 * cleanup runs after the commit that took the panel out, so `<body>` stands in for
 * "the reader was inside". An approximation: a click on blank space, or a press in
 * Safari, also leaves `<body>`, and closing from there gives focus to the opener
 * though the reader never went in. Anywhere else they have gone on, to the composer
 * or a sheet rising through this one's leave, and stay there.
 *
 * A new `press` means another press has pointed the surface, from any row and at
 * any thread, so the opener is captured again and nothing else happens. That is an
 * effect of its own: were the restore its cleanup, Safari, where pressing a button
 * does not focus it, would sit on `<body>` through the press and pull focus to the
 * last opener.
 *
 * A breakpoint's remount (`Handoff.up` already set) is no open and no close. The
 * opener stays the one the press captured, and the mode going gives nothing back.
 * The mode arriving takes focus only from `<body>`: that is where the removal of
 * the panel it replaces left a reader who was inside, and where nothing else says
 * where a reader was. Anyone elsewhere, in the composer mid-sentence say, stays.
 *
 * `active` is true only while the panel is rendered, so its ref is set by the time
 * this runs.
 */
export function useAsideFocus(
  active: boolean,
  panel: RefObject<HTMLElement | null>,
  take: boolean,
  handoffRef: RefObject<Handoff>,
  press?: number | null,
): void {
  // The press this mode has seen, so a remount does not count as a re-point.
  const seen = useRef(press);

  useEffect(() => {
    if (!active || press === seen.current) return;
    seen.current = press;
    handoffRef.current.opener = document.activeElement;
  }, [active, press, handoffRef]);

  useEffect(() => {
    const node = panel.current;
    if (!active || !node) return;
    const state = handoffRef.current;
    const crossing = state.up;
    state.up = true;
    if (!crossing) state.opener = document.activeElement;
    if (crossing ? document.activeElement === document.body : take) {
      node.focus({ preventScroll: true });
    }
    return () => {
      if (state.open) return;
      state.up = false;
      if (document.activeElement === document.body) restoreFocus(state.opener);
    };
  }, [active, panel, take, handoffRef]);
}

type RiseStyle = CSSProperties & { "--layer-duration": string };

/** The theme's surface colours and the duration `.rise-in`/`.rise-out` read. */
export function riseStyle(durationMs: number): RiseStyle {
  return { background: "var(--bg)", color: "var(--fg)", "--layer-duration": `${durationMs}ms` };
}

export function riseClass(leaving: boolean): string {
  return leaving ? "rise-out" : "rise-in";
}
