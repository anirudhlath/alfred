import { useEffect, useState, type CSSProperties, type RefObject } from "react";

/** The handoff's reduce-motion substitute for every rise. */
const REDUCED_MOTION_MS = 200;

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
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

/** How many modal surfaces are up: the app behind them is inert until the last one goes. */
let surfacesUp = 0;

/**
 * While a modal surface is up, the app behind it is inert and focus lives in
 * the surface — Full Keyboard Access and VoiceOver must not wander into a room
 * they cannot see. Focus goes back where it was once the surface has left.
 *
 * `active` is the presence's `mounted`, so the surface stays inert-backed for
 * its leave animation too, and the panel ref is set by the time this runs.
 */
export function useModalFocus(active: boolean, panel: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    if (!active) return;
    const app = document.getElementById("root");
    const previous = document.activeElement;
    surfacesUp += 1;
    app?.setAttribute("inert", "");
    panel.current?.focus({ preventScroll: true });
    return () => {
      surfacesUp -= 1;
      if (surfacesUp === 0) app?.removeAttribute("inert");
      if (previous instanceof HTMLElement && previous.isConnected) {
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
