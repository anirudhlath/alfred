import type { ReactNode } from "react";
import { GateField } from "@/gates/GateField";

export interface GateAction {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  busy?: boolean;
}

export interface GateProps {
  kicker: string;
  title: string;
  body?: string;
  /** A step list, a credential form — anything between the body and the footer. */
  children?: ReactNode;
  primary?: GateAction;
  secondary?: { label: string; onClick: () => void };
  foot?: string;
}

export function Gate({ kicker, title, body, children, primary, secondary, foot }: GateProps) {
  return (
    <div
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)", color: "var(--fg)" }}
    >
      <GateField />

      {/* Copy sits at the bottom of the field, not the middle: padding 0 28 12. */}
      <div className="relative flex flex-1 flex-col justify-end gap-3 px-7 pb-3">
        <div className="t-meta">{kicker}</div>
        <h1 className="t-gate">{title}</h1>
        {body ? (
          <p className="t-body" style={{ color: "var(--fg2)" }}>
            {body}
          </p>
        ) : null}
        {children}
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
            onClick={secondary.onClick}
            className="h-[50px] rounded-[25px] border-0 bg-transparent text-[15px] font-medium"
            style={{ color: "var(--fg)" }}
          >
            {secondary.label}
          </button>
        ) : null}

        {foot ? <div className="t-meta text-center">{foot}</div> : null}
      </div>
    </div>
  );
}
