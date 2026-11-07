import { useEffect, useId, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useDeskSlots } from "@/shell/Desk";
import { useLayout } from "@/shell/layout";
import { riseClass, riseStyle, useAsideFocus, useModalFocus, usePresence } from "@/shell/presence";

const SHEET_MS = 380;

export interface SheetProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** The line over the title. Only a column draws one. */
  eyebrow?: string;
  /** What the surface is for: the body's first line, or the column header's last. */
  intro?: string;
  /**
   * What the detail is showing, by identity, where another row can re-point it. Beside
   * the app, a new one starts the body at the top; `null` is a close, and a sheet leaves
   * from where it was. In a column it also makes that row the opener focus returns to.
   * Not over the bench, where focus is in the panel, and the panel is no opener. The
   * phone ignores it: nothing behind its modal can press a row.
   */
  subject?: unknown;
}

/**
 * A detail, wherever the layout puts it (handoff §11): the third column from
 * 1100 px, a sheet over the bench from 760 px, and a modal sheet on the phone,
 * which is also what a width gets before `Desk` has handed out its slot.
 *
 * One component per mode, so each has its own hooks. Crossing a breakpoint with a
 * detail open remounts it in the new mode, and its focus moves the way an open and
 * a close would.
 */
export function Sheet(props: SheetProps) {
  const layout = useLayout();
  const { overBench, detail } = useDeskSlots();
  if (layout === "columns" && detail) return <DetailColumn {...props} slot={detail} />;
  if (layout === "sheet-over-bench" && overBench) {
    return <SheetOverBench {...props} slot={overBench} />;
  }
  return <ModalSheet {...props} />;
}

function ModalSheet(props: SheetProps) {
  const { open, onClose } = props;
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
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
    <SheetPanel {...props} panel={panel} box="viewport-fill" modal leaving={leaving} />,
    document.body,
  );
}

/**
 * The phone's sheet, over the bench alone: the Room beside it stays live, so this
 * is a dialog without `aria-modal`, and `Desk` quiets only the bench behind it.
 */
function SheetOverBench({ slot, ...props }: SheetProps & { slot: HTMLElement }) {
  const { open, subject, onClose } = props;
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
  const body = useTopOnRepoint(subject);
  useAsideFocus(mounted, panel, true);
  useEscapeBeside(open, panel, onClose);

  if (!mounted) return null;

  return createPortal(
    <SheetPanel {...props} panel={panel} body={body} box="absolute inset-0" leaving={leaving} />,
    slot,
  );
}

/**
 * Handoff §11's third column, `Alfred Desktop.dc.html:93-98`. It has no leave: the
 * column appearing is a change of the grid (`.desk` in `index.css`), so it is gone
 * the moment `open` is false.
 */
function DetailColumn({
  open,
  title,
  eyebrow,
  intro,
  subject,
  onClose,
  children,
  slot,
}: SheetProps & { slot: HTMLElement }) {
  const panel = useRef<HTMLElement>(null);
  const body = useTopOnRepoint(subject);
  const titleId = useId();
  useAsideFocus(open, panel, false, subject);
  useEscapeBeside(open, panel, onClose);

  if (!open) return null;

  return createPortal(
    <aside ref={panel} aria-labelledby={titleId} className="flex min-h-0 flex-1 flex-col">
      <header
        className="flex flex-col gap-1.5 px-6 pt-10 pb-3.5"
        style={{ borderBottom: "1px solid var(--line)" }}
      >
        <div className="flex items-center justify-between">
          {eyebrow ? (
            // The mock's type, with only its colour moved: `.t-label`'s own `--muted`
            // is 3.34:1 on `--detail` in light, under AA at 11 px.
            <span className="t-label" style={{ color: "var(--fg2)" }}>
              {eyebrow}
            </span>
          ) : null}
          {/* `ml-auto`, or Held back, with no eyebrow, starts it on the left. A real
              button because an iPad reaches this width with no Escape key, in the
              mock's mono 11 px with `--fg2` for its `--muted`: a button's name has to
              be readable. The `after` box makes 44 px of its 16.5 px line. */}
          <button
            type="button"
            onClick={onClose}
            className="t-meta-strong relative ml-auto border-0 bg-transparent p-0 after:absolute after:inset-x-0 after:-inset-y-3.5 after:content-['']"
          >
            esc closes
          </button>
        </div>
        <h2 id={titleId} className="t-title">
          {title}
        </h2>
        {intro ? <Intro text={intro} /> : null}
      </header>
      <div
        ref={body}
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-6 pt-[18px] pb-10"
      >
        {children}
      </div>
    </aside>,
    slot,
  );
}

interface SheetPanelProps extends SheetProps {
  panel: RefObject<HTMLDivElement | null>;
  body?: RefObject<HTMLDivElement | null>;
  /** How the panel's outer box fills what it is over. */
  box: string;
  modal?: boolean;
  leaving: boolean;
}

/**
 * Bottom-anchored sheet over a 45% scrim: grab bar, title, `Done`. The scrim,
 * `Done` and the Escape key all dismiss it — constraint §4.12, a standalone
 * app has no browser chrome to escape with.
 */
function SheetPanel({
  title,
  intro,
  onClose,
  children,
  panel,
  body,
  box,
  modal,
  leaving,
}: SheetPanelProps) {
  const titleId = useId();

  return (
    // The dialog is the whole thing, scrim included, so the scrim's `Close` is
    // inside the dialog's subtree and assistive tech can reach it. z-20, over the
    // Workshop's z-10 and under Layer's z-30: the handoff stacks workshop <
    // sheet < Door < gate, so a sheet opened from the Workshop paints over it,
    // and a critical action or a lapsed session paints over the sheet in turn.
    <div
      ref={panel}
      tabIndex={-1}
      role="dialog"
      aria-modal={modal ? "true" : undefined}
      aria-labelledby={titleId}
      className={`${box} z-20 flex flex-col justify-end outline-none`}
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
            // `--accent-text`, not `--accent`: the raw accent is 2.34:1 on
            // paper's `--bg` (index.css, --accent-text).
            style={{ color: "var(--accent-text)" }}
          >
            Done
          </button>
        </div>
        <div
          ref={body}
          className="flex flex-1 flex-col gap-3 overflow-y-auto px-5"
          style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
        >
          {intro ? <Intro text={intro} /> : null}
          {children}
        </div>
      </div>
    </div>
  );
}

function Intro({ text }: { text: string }) {
  return (
    <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
      {text}
    </p>
  );
}

/**
 * The ref for a detail's body, which goes back to the top on every press that points
 * the detail: halfway down one thread is nowhere in the next, and a cached one renders
 * straight in, so nothing else would move the old offset. A press for the thread
 * already open asks to see it, so it goes to the top as well. No subject is a close, not
 * a press: a sheet's leave is still on screen, and it should not jump as it goes.
 */
function useTopOnRepoint(subject: unknown): RefObject<HTMLDivElement | null> {
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (subject != null && body.current) body.current.scrollTop = 0;
  }, [subject]);
  return body;
}

/**
 * Escape from anywhere, for a detail that sits beside the app: focus can be in the
 * Room or the bench, so the key would not reach the panel the way it reaches the
 * phone's. A Door or a gate makes `#root` inert around the panel, and then the key
 * is not this detail's: nothing behind a modal surface answers to it. Nor is a
 * composition's Escape, which cancels the candidate of someone still writing.
 */
function useEscapeBeside(
  active: boolean,
  panel: RefObject<HTMLElement | null>,
  onClose: () => void,
): void {
  useEffect(() => {
    if (!active) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !event.isComposing && !panel.current?.closest("[inert]")) onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [active, panel, onClose]);
}
