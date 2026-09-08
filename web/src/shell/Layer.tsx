import { useEffect, useState, type CSSProperties, type ReactNode } from "react";

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
// eslint-disable-next-line react-refresh/only-export-components
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

export interface LayerProps {
  open: boolean;
  /** The dialog's accessible name — standalone mode has no window title to fall back on. */
  label: string;
  /** Sheets 380, workshop 400, door 420. */
  durationMs?: number;
  className?: string;
  children: ReactNode;
}

export function Layer({ open, label, durationMs = 400, className = "", children }: LayerProps) {
  const { mounted, leaving } = usePresence(open, durationMs);
  if (!mounted) return null;

  const style = {
    background: "var(--bg)",
    color: "var(--fg)",
    "--layer-duration": `${durationMs}ms`,
  } as CSSProperties;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={label}
      className={`fixed inset-0 z-30 flex flex-col overflow-hidden ${leaving ? "rise-out" : "rise-in"} ${className}`}
      style={style}
    >
      {children}
    </div>
  );
}
