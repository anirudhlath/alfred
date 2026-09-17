export interface TombstoneProps {
  title: string;
  meta: string;
}

/**
 * What is left of an approval nobody answered. Struck through, on `surface`, at
 * 80% — present, finished, and unmistakably not done.
 */
export function Tombstone({ title, meta }: TombstoneProps) {
  return (
    <div
      className="flex items-start gap-3 rounded-xl px-3 py-2.5 opacity-80"
      style={{ background: "var(--surface)" }}
    >
      <span
        aria-hidden="true"
        className="mt-[5px] box-border h-2 w-2 shrink-0 rounded-full border-[1.5px]"
        style={{ borderColor: "var(--muted)" }}
      />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="t-row line-through" style={{ color: "var(--fg2)" }}>
          {title}
        </div>
        <div className="t-meta">{meta}</div>
      </div>
    </div>
  );
}
