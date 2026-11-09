import { ring } from "@/lib/streams";

export interface ActRowProps {
  /** 120 trigger (EV) · 210 reflex (RX) · 255 notification (NT). */
  hue: 120 | 210 | 255;
  text: string;
  meta: string;
  /** Present only on rows with a cause to trace: renders the `why?` button. */
  onWhy?: () => void;
  /** This row's causal thread is open beside the Room (`Alfred Desktop.dc.html:32`). */
  threadOpen?: boolean;
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
 *
 * With its thread open the row draws no `why?`, as the mock does, but the button
 * stays in the DOM and only fades: removing the node a keyboard reader has just
 * pressed would throw their focus to `<body>`. Faded, it takes no pointer: it is
 * invisible, and pressing it does nothing. The fade takes the button's column with
 * it: it leaves the flow for the spot its margins held (10 px of padding less 10 up,
 * 12 px less 6 out), so the row's words get all its width, as the mock's do, and
 * opening a thread does not grow the row by a line.
 */
export function ActRow({ hue, text, meta, onWhy, threadOpen = false }: ActRowProps) {
  return (
    <div
      className={`flex items-start gap-3 py-2.5 ${threadOpen ? "relative -mx-3 rounded-[10px] px-3" : ""}`}
      style={{
        borderTop: "1px solid var(--line)",
        borderBottom: "1px solid var(--line)",
        background: threadOpen ? "var(--surface)" : undefined,
      }}
    >
      <span
        aria-hidden="true"
        data-testid="act-mark"
        data-hue={hue}
        className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
        style={{ background: ring(hue) }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row" style={{ color: threadOpen ? "var(--fg)" : "var(--fg2)" }}>
          {text}
        </div>
        {/* `t-meta-strong` while open: these are the only words saying so, and
            `--muted` is 3.20:1 on `--surface` in light (index.css). */}
        <div className={threadOpen ? "t-meta-strong" : "t-meta"}>
          {threadOpen ? `${meta} · thread open →` : meta}
        </div>
      </div>
      {onWhy ? (
        <button
          type="button"
          onClick={onWhy}
          // Every act row's button says the same two words, so the visible
          // label alone would read `why?, why?, why?` down a rotor. The row's
          // own line names which one this is, and the visible text stays a
          // prefix of the name (WCAG 2.5.3, label in name).
          aria-label={`why? ${text}`}
          className={`${threadOpen ? "pointer-events-none absolute top-0 right-1.5 opacity-0 focus-visible:opacity-100" : "-mt-2.5 -mr-1.5"} flex h-11 shrink-0 items-center border-0 bg-transparent px-2.5 text-[13px] font-medium`}
          style={{ color: "var(--accent-text)" }}
        >
          why?
        </button>
      ) : null}
    </div>
  );
}
