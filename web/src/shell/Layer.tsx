import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { riseClass, riseStyle, useModalFocus, usePresence } from "./presence";

export interface LayerProps {
  open: boolean;
  /** The dialog's accessible name — standalone mode has no window title to fall back on. */
  label: string;
  /** Sheets 380, workshop 400, door 420. */
  durationMs?: number;
  /**
   * The handoff's stack, sheet < door < gate: a lapsed session paints over an
   * open Door, whatever order they were mounted in.
   */
  level?: "layer" | "gate";
  className?: string;
  children: ReactNode;
}

/**
 * A full-screen surface that rises from the bottom, stays for its leave
 * animation, and holds focus while it is up. Rendered into `<body>` so the
 * app in `#root` can be made inert behind it.
 */
export function Layer({
  open,
  label,
  durationMs = 400,
  level = "layer",
  className = "",
  children,
}: LayerProps) {
  const { mounted, leaving } = usePresence(open, durationMs);
  const panel = useRef<HTMLDivElement>(null);
  useModalFocus(mounted, panel);
  if (!mounted) return null;

  return createPortal(
    <div
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label={label}
      className={`fixed inset-0 ${level === "gate" ? "z-40" : "z-30"} flex flex-col overflow-hidden outline-none ${riseClass(leaving)} ${className}`}
      style={riseStyle(durationMs)}
    >
      {children}
    </div>,
    document.body,
  );
}
