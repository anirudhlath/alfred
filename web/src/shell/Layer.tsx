import { useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { riseClass, riseStyle, useModalFocus, usePresence } from "@/shell/presence";

export interface LayerProps {
  open: boolean;
  /** The dialog's accessible name — standalone mode has no window title to fall back on. */
  label: string;
  /** Sheets 380, workshop 400, door 420. */
  durationMs?: number;
  /**
   * The handoff's stack, workshop < sheet < door < gate: a sheet opened from
   * the Workshop paints over it, and a lapsed session paints over everything,
   * whatever order they were mounted in.
   */
  level?: "workshop" | "layer" | "gate";
  className?: string;
  children: ReactNode;
}

const Z_INDEX: Record<NonNullable<LayerProps["level"]>, string> = {
  workshop: "z-10",
  layer: "z-30",
  gate: "z-40",
};

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
      className={`fixed inset-0 ${Z_INDEX[level]} flex flex-col overflow-hidden outline-none ${riseClass(leaving)} ${className}`}
      style={riseStyle(durationMs)}
    >
      {children}
    </div>,
    document.body,
  );
}
