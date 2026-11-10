import { useEffect, useId, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { useDeskSlots } from "@/shell/Desk";
import { useLayout } from "@/shell/layout";
import {
  riseClass,
  riseStyle,
  useAsideFocus,
  useModalFocus,
  usePresence,
  type Handoff,
} from "@/shell/presence";

const SHEET_MS = 380;

/**
 * The `tabIndex` of a detail's body beside the app, the column's and the sheet's over
 * the bench. A click in the body focuses the nearest focusable thing around it, and
 * without one of its own that is the panel, which the keys cannot scroll: Space,
 * PageDown and the arrows start at the focused element and walk up, and the body is
 * below it. Focused itself, the body is what they scroll.
 *
 * `0` and not `-1`: Chromium makes a scroller with nothing focusable in it, a thread,
 * a Tab stop of its own, and any `tabIndex` overrides that, so `-1` would take a
 * keyboard reader's only way to scroll the thread. A mouse focuses it with no ring,
 * since `:focus-visible` leaves out a click on anything that takes no typing. The
 * panel stays what a breakpoint's crossing focuses, being the element a screen reader
 * names.
 */
const SCROLLER_FOCUS = 0;

/**
 * What a detail's body beside the app carries, and the phone's does not. The mark is
 * for `index.css`, which gives it the rows' inset ring: the UA's is drawn outside the
 * box, and the column's and the sheet's `overflow: hidden` leave 1 px of it.
 */
const DESK_BODY = { tabIndex: SCROLLER_FOCUS, "data-detail-body": "" } as const;

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
   * Which press pointed the detail: a new number for every press, the open detail's own
   * row included (`useDetail`). Beside the app, a new one starts the body at the top;
   * `null` is a close, and a sheet leaves from where it was. In a column it also makes
   * the row pressed the opener focus returns to. Not over the bench, where focus is in
   * the panel, and the panel is no opener. The phone ignores it: nothing behind its
   * modal can press a row.
   */
  press?: number | null;
}

/**
 * A detail, wherever the layout puts it (handoff §11): the third column from
 * 1100 px, a sheet over the bench from 760 px, and a modal sheet on the phone,
 * which is also what a width gets before `Desk` has handed out its slot.
 *
 * One component per mode, so each has its own hooks. Crossing a breakpoint with a
 * detail open remounts it in the new mode, and that is neither an open nor a close
 * (`Handoff`): the opener stays the row pressed, and focus moves in only where it
 * has to. That is into a modal, which makes everything behind it inert, or for a
 * reader who was inside the panel that went, or who was on `<body>`, or on a bench row
 * the sheet over the bench has just made inert. A reader in the composer stays there.
 *
 * At width the slot opens on the Room's state alone: `Desk`'s `detailOpen` is
 * `heldOpen || why` (`Room.tsx`). A `Sheet` opened anywhere else inside the `Desk`
 * would render a `DetailColumn` into a slot the grid keeps hidden, and a sheet over
 * the bench with the bench behind it still live. A new caller opens through the
 * Room's detail (`useDetail`), or adds a way for a `Sheet` to register with `Desk`.
 */
export function Sheet(props: SheetProps) {
  const layout = useLayout();
  const { overBench, detail } = useDeskSlots();
  const handoff = useRef<Handoff>({ opener: null, up: false, open: false });
  const { open } = props;
  // A layout effect, so it is current by the passive cleanups of the commit that
  // closes. No cleanup: StrictMode's rehearsal of an unmount would read as a close,
  // and a detail truly unmounted while open has taken its opener with it.
  useLayoutEffect(() => {
    handoff.current.open = open;
  }, [open]);

  if (layout === "columns" && detail) {
    return <DetailColumn {...props} slot={detail} handoff={handoff} />;
  }
  if (layout === "sheet-over-bench" && overBench) {
    return <SheetOverBench {...props} slot={overBench} handoff={handoff} />;
  }
  return <ModalSheet {...props} handoff={handoff} />;
}

interface ModeProps extends SheetProps {
  handoff: RefObject<Handoff>;
}

function ModalSheet({ handoff, ...props }: ModeProps) {
  const { open, onClose } = props;
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
  useModalFocus(mounted, panel, handoff);

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
function SheetOverBench({ slot, handoff, ...props }: ModeProps & { slot: HTMLElement }) {
  const { open, press, onClose } = props;
  const { mounted, leaving } = usePresence(open, SHEET_MS);
  const panel = useRef<HTMLDivElement>(null);
  const body = useTopOnRepoint(press);
  useAsideFocus(mounted, panel, true, handoff);
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
 *
 * `tabIndex={-1}` so a reader who was in the sheet over the bench can follow it here
 * across 1100 px: the aside is what a screen reader names as the thread. An open never
 * focuses it, and a click in the thread does not land on it, because the body is
 * focusable itself (`SCROLLER_FOCUS`).
 */
function DetailColumn({
  open,
  title,
  eyebrow,
  intro,
  press,
  onClose,
  children,
  slot,
  handoff,
}: ModeProps & { slot: HTMLElement }) {
  const panel = useRef<HTMLElement>(null);
  const body = useTopOnRepoint(press);
  const titleId = useId();
  useAsideFocus(open, panel, false, handoff, press);
  useEscapeBeside(open, panel, onClose);

  if (!open) return null;

  return createPortal(
    <aside
      ref={panel}
      tabIndex={-1}
      aria-labelledby={titleId}
      className="flex min-h-0 flex-1 flex-col outline-none"
    >
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
        {...DESK_BODY}
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
          // Over the bench only. The phone's modal keeps its DOM, and the focus VoiceOver
          // was device-tested with: a press there lands on the dialog, as it always has.
          {...(modal ? {} : DESK_BODY)}
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
 * already open asks to see it, so it goes to the top as well, which is why this counts
 * presses rather than comparing what they point at. No press is a close: a sheet's leave
 * is still on screen, and it should not jump as it goes.
 */
function useTopOnRepoint(press: number | null | undefined): RefObject<HTMLDivElement | null> {
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (press != null && body.current) body.current.scrollTop = 0;
  }, [press]);
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
