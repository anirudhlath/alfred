import type { ReactNode } from "react";
import { GateField } from "@/gates/GateField";

export interface GateAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export interface GateSecondaryAction extends Omit<GateAction, "busy"> {
  /**
   * Its request is in flight: `aria-disabled` and `aria-busy`, a press that does
   * nothing, and the label dimmed by token — to `--fg2`, the gate body's own
   * colour, which still reads as text on the page (`test/contrast.test.ts`).
   * Not `disabled`, which would throw focus to `<body>` from under the finger
   * that just pressed it (as `ActivityBench`'s older-entries button says).
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
  body,
  children,
  primary,
  secondary,
  foot,
}: GateProps) {
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
          <h1 className="t-gate">{title}</h1>
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
            onClick={primary.onClick}
            disabled={primary.disabled === true || primary.busy === true}
            aria-busy={primary.busy === true}
            className="h-14 rounded-[28px] border-0 text-[16px] font-medium disabled:opacity-60"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            {primary.label}
          </button>
        ) : null}

        {secondary ? (
          <button
            type="button"
            onClick={() => {
              // `aria-disabled` does not stop the event, so this does.
              if (secondary.waiting !== true) secondary.onClick();
            }}
            disabled={secondary.disabled === true}
            aria-disabled={secondary.waiting === true ? true : undefined}
            aria-busy={secondary.waiting === true ? true : undefined}
            className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium disabled:opacity-60"
            style={{ color: secondary.waiting === true ? "var(--fg2)" : "var(--fg)" }}
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
