import { useEffect, useRef, type ReactNode } from "react";
import { GateField } from "@/gates/GateField";

/*
 * Neither button is ever given a native `disabled`. A held button is
 * `aria-disabled`, its press does nothing (the click handler checks, because
 * `aria-disabled` stops no event), and it is dimmed by token, never by opacity
 * (`docs/web-frontend.md`, "The rules this phase's reviews kept enforcing").
 * `disabled` would throw focus to `<body>` from under the finger that just
 * pressed the button, and every primary a gate passes here goes busy on its
 * own press.
 */

export interface GateAction {
  label: string;
  onClick: () => void;
  /**
   * The action cannot be taken yet: a credential form with no fields, a list
   * still being read. The handoff draws no state for this, so the house's is a
   * `--surface` fill with an `--fg2` label and an inset 1 px `--muted` edge —
   * `--surface` alone is barely a step off the page, and the edge is what still
   * shows where the button is. All three pairs are in `test/contrast.test.ts`.
   */
  disabled?: boolean;
  /**
   * Its request is in flight. `aria-busy` as well, and still ink on paper: a
   * busy button is answering the press it took, not unavailable.
   */
  busy?: boolean;
}

export interface GateSecondaryAction extends Omit<GateAction, "busy" | "disabled"> {
  /**
   * It cannot be taken now: setup's `Do this later` while the credential write
   * is in flight. Held as `waiting` holds it — `aria-disabled`, a press that
   * does nothing, the label at `--fg2` — but without `aria-busy`, since nothing
   * of this action's own is in flight. No fill and no edge: this is a text
   * button, not the primary's unavailable state.
   */
  disabled?: boolean;
  /**
   * Its request is in flight: held as `disabled` holds it, with `aria-busy` as
   * well. The label dims to `--fg2`, the gate body's own colour, which still
   * reads as text on the page (`test/contrast.test.ts`).
   */
  waiting?: boolean;
}

export interface GateProps {
  kicker: string;
  /**
   * Set the kicker in `.t-meta-strong` rather than `.t-meta`: for a gate whose
   * kicker a reader must be able to take on its own to trust the screen
   * (index.css says where that line is drawn). The offline gate's and the
   * unanswered gate's, so far — each is the line that says which silence this is.
   */
  strongKicker?: boolean;
  title: string;
  /**
   * Move focus to the title when the gate mounts. For a gate that takes the
   * place of another under the hand that pressed — setup's later steps — so
   * the press lands on a heading that says where it went, which a screen
   * reader then reads, and not on whatever control now stands where the
   * pressed one was. The title is focusable from script only (`tabIndex` -1).
   */
  focusTitle?: boolean;
  body?: string;
  /** A step list, a credential form — anything between the body and the footer. */
  children?: ReactNode;
  primary?: GateAction;
  secondary?: GateSecondaryAction;
  /**
   * Rendered as a status region: the gates put their errors and caveats here,
   * and a change to it must be spoken, not just painted.
   */
  foot?: string;
}

export function Gate({
  kicker,
  strongKicker = false,
  title,
  focusTitle = false,
  body,
  children,
  primary,
  secondary,
  foot,
}: GateProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (focusTitle) heading.current?.focus();
  }, [focusTitle]);

  const primaryUnavailable = primary?.disabled === true;
  const primaryHeld = primaryUnavailable || primary?.busy === true;
  const secondaryHeld = secondary?.disabled === true || secondary?.waiting === true;

  return (
    <div
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
    >
      <GateField />

      {/* Copy sits at the bottom of the field, not the middle: padding 0 28 12.

          It scrolls, because the shell is now a fixed height (§4.4) and its
          parent is a clipper, not a scroller — there is no document scroll left
          to borrow. The setup gate's credential step is the tall one: ~560px of
          kicker, title, body, progress list and two fields, which does not fit a
          375x553 viewport once the footer is paid for.

          `mt-auto` on an inner column rather than `justify-end` out here: end
          alignment pushes the overflow off the *top*, and start-side overflow is
          not in the scrollable region — measured at 375x553, justify-end reports
          scrollHeight equal to clientHeight with the kicker 197px above the edge
          and no way to reach it, where this reports 363/560 and scrolls. An auto
          margin resolves to 0 once the free space is negative, so short copy
          still sits on the bottom. */}
      <div className="relative flex flex-1 flex-col overflow-y-auto px-7 pb-3">
        <div className="mt-auto flex flex-col gap-3">
          <div className={strongKicker ? "t-meta-strong" : "t-meta"}>{kicker}</div>
          {/* No ring on the title when it takes focus, as on the Layer and the
              Sheet that take it the same way: it is a place, not a control. */}
          <h1
            ref={heading}
            tabIndex={focusTitle ? -1 : undefined}
            className="t-gate outline-none"
          >
            {title}
          </h1>
          {body ? (
            <p className="t-body" style={{ color: "var(--fg2)" }}>
              {body}
            </p>
          ) : null}
          {children}
        </div>
      </div>

      <div
        className="relative flex flex-col gap-2.5 px-5 pt-2"
        style={{ paddingBottom: "calc(40px + env(safe-area-inset-bottom, 0px))" }}
      >
        {primary ? (
          <button
            type="button"
            onClick={() => {
              // `aria-disabled` does not stop the event, so this does.
              if (!primaryHeld) primary.onClick();
            }}
            aria-disabled={primaryHeld ? true : undefined}
            aria-busy={primary.busy === true ? true : undefined}
            className="h-14 rounded-[28px] border-0 text-[16px] font-medium"
            style={
              primaryUnavailable
                ? {
                    background: "var(--surface)",
                    color: "var(--fg2)",
                    boxShadow: "inset 0 0 0 1px var(--muted)",
                  }
                : { background: "var(--ink)", color: "var(--paper)" }
            }
          >
            {primary.label}
          </button>
        ) : null}

        {secondary ? (
          <button
            type="button"
            onClick={() => {
              if (!secondaryHeld) secondary.onClick();
            }}
            aria-disabled={secondaryHeld ? true : undefined}
            aria-busy={secondary.waiting === true ? true : undefined}
            className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium"
            style={{ color: secondaryHeld ? "var(--fg2)" : "var(--fg)" }}
          >
            {secondary.label}
          </button>
        ) : null}

        {foot ? (
          <div role="status" className="t-meta text-center">
            {foot}
          </div>
        ) : null}
      </div>
    </div>
  );
}
