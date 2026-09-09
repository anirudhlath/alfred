import { useEffect, useId, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { riseClass, riseStyle, useModalFocus, usePresence } from "@/shell/presence";

const SHEET_MS = 380;

export interface SheetProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/**
 * Bottom-anchored sheet over a 45% scrim: grab bar, title, `Done`. The scrim,
 * `Done` and the Escape key all dismiss it — constraint §4.12, a standalone
 * app has no browser chrome to escape with.
 */
export function Sheet({ open, title, onClose, children }: SheetProps) {
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
  const titleId = useId();
  useModalFocus(mounted, panel);

  // On the panel, not `document`: the sheet holds focus while it is on top, so
  // the key reaches it, and a gate over it swallows Escape instead.
  useEffect(() => {
    const node = panel.current;
    if (!mounted || !node) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    node.addEventListener("keydown", onKeyDown);
    return () => node.removeEventListener("keydown", onKeyDown);
  }, [mounted, onClose]);

  if (!mounted) return null;

  return createPortal(
    // The dialog is the whole thing, scrim included, so the scrim's `Close` is
    // inside the modal subtree and assistive tech can reach it. z-20, under
    // Layer's z-30: the handoff stacks sheet < Door < gate, so a critical action
    // or a lapsed session paints over an open sheet, not under it.
    <div
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
      className="fixed inset-0 z-20 flex flex-col justify-end outline-none"
    >
      <button
        type="button"
        aria-label="Close"
        onClick={onClose}
        className="absolute inset-0 border-0"
        style={{ background: "var(--scrim)" }}
      />
      <div
        className={`relative flex max-h-[78%] flex-col overflow-hidden rounded-t-[28px] ${riseClass(leaving)}`}
        style={riseStyle(SHEET_MS)}
      >
        <div
          aria-hidden="true"
          className="mx-auto mt-2 h-[5px] w-10 rounded-full"
          style={{ background: "var(--line)" }}
        />
        <div className="flex items-center justify-between gap-3 px-5 pt-2 pb-3">
          <h2 id={titleId} className="t-title">
            {title}
          </h2>
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
    </div>,
    document.body,
  );
}
