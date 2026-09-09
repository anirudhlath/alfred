export interface ActRowProps {
  /** 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT). */
  hue: 120 | 210 | 255;
  text: string;
  meta: string;
}

/**
 * Something Alfred did while you were not asking. Same thread, quieter voice:
 * `fg2` at 14.5, hairlines above and below, and an 8 px mark in the stream's own
 * hue so which mind acted is legible at a glance (spec §5.2.4).
 */
export function ActRow({ hue, text, meta }: ActRowProps) {
  return (
    <div
      className="flex items-start gap-3 py-2.5"
      style={{ borderTop: "1px solid var(--line)", borderBottom: "1px solid var(--line)" }}
    >
      <span
        aria-hidden="true"
        data-testid="act-mark"
        data-hue={hue}
        className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
        style={{ background: `oklch(0.62 0.11 ${hue})` }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row" style={{ color: "var(--fg2)" }}>
          {text}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
    </div>
  );
}
