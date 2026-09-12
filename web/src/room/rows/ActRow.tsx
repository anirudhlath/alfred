import { ring } from "@/lib/streams";

export interface ActRowProps {
  /** 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT). */
  hue: 120 | 210 | 255;
  text: string;
  meta: string;
  /** Present only on rows with a cause to trace: renders the `why?` button. */
  onWhy?: () => void;
}

/**
 * Something Alfred did while you were not asking. Same thread, quieter voice:
 * `fg2` at 14.5, hairlines above and below, and an 8 px mark in the stream's own
 * hue so which mind acted is legible at a glance (spec §5.2.4).
 *
 * The optional `why?` is the handoff's — accent 13/500 with a 44 px hit area,
 * pulled up and out by its own padding so the row's rhythm does not change.
 * `--accent-text`, not `--accent`: the raw accent is 2.34:1 on paper's `--bg`
 * (index.css, --accent-text), and no `color: var(--accent)` is left in the app.
 */
export function ActRow({ hue, text, meta, onWhy }: ActRowProps) {
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
        style={{ background: ring(hue) }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row" style={{ color: "var(--fg2)" }}>
          {text}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
      {onWhy ? (
        <button
          type="button"
          onClick={onWhy}
          className="-mt-2.5 -mr-1.5 h-11 shrink-0 border-0 bg-transparent px-2.5 text-[13px] font-medium"
          style={{ color: "var(--accent-text)" }}
        >
          why?
        </button>
      ) : null}
    </div>
  );
}
