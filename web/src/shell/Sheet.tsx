import type { CSSProperties, ReactNode } from "react";
import { usePresence } from "@/shell/Layer";

const SHEET_MS = 380;

export interface SheetProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Bottom-anchored sheet over a 45% scrim: grab bar, title, `Done`. Both the
 * scrim and `Done` dismiss it — constraint §4.12, a standalone app has no
 * browser chrome to escape with.
 */
export function Sheet({ open, title, onClose, children }: SheetProps) {
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  if (!mounted) return null;

  const panelStyle = {
    background: "var(--bg)",
    color: "var(--fg)",
    "--layer-duration": `${SHEET_MS}ms`,
  } as CSSProperties;

  return (
    // z-20, under Layer's z-30: the handoff stacks sheet < Door < gate, so a
    // critical action or a lapsed session paints over an open sheet, not under it.
    <div className="fixed inset-0 z-20 flex flex-col justify-end">
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 border-0"
        style={{ background: "var(--scrim)" }}
      />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`relative flex max-h-[78%] flex-col overflow-hidden rounded-t-[28px] ${leaving ? "rise-out" : "rise-in"}`}
        style={panelStyle}
      >
        <div
          aria-hidden="true"
          className="mx-auto mt-2 h-[5px] w-10 rounded-full"
          style={{ background: "var(--line)" }}
        />
        <div className="flex items-center justify-between gap-3 px-5 pt-2 pb-3">
          <h2 className="t-title">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            className="-mr-2 flex h-11 min-w-11 items-center justify-end border-0 bg-transparent text-[15px] font-medium"
            style={{ color: "var(--accent)" }}
          >
            Done
          </button>
        </div>
        <div
          className="flex flex-1 flex-col gap-3 overflow-y-auto px-5"
          style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
        >
          {children}
        </div>
      </div>
    </div>
  );
}
