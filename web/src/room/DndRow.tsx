import { hhmm } from "@/lib/format";

export interface DndRowProps {
  /** ISO 8601, or null for "no expiry" — the queue that never drains on its own. */
  until: string | null | undefined;
  heldCount: number;
  onOpen: () => void;
}

/**
 * The only place the Room admits it is being quiet. Opens the Held-back sheet,
 * because a deferred queue you cannot see is the failure spec §5.2.6 names.
 */
export function DndRow({ until, heldCount, onOpen }: DndRowProps) {
  const stamp = until ? hhmm(until) : "--:--";
  const label =
    stamp === "--:--" ? "Do-not-disturb · no expiry" : `Do-not-disturb until ${stamp}`;

  return (
    <button
      type="button"
      onClick={onOpen}
      className="mt-2 flex min-h-11 w-full items-center justify-between gap-2 rounded-[10px] border px-3 py-2.5 text-left text-[13px]"
      style={{ borderColor: "var(--line)", background: "transparent", color: "var(--fg)" }}
    >
      <span>{label}</span>
      <span className="t-meta">{heldCount} held ›</span>
    </button>
  );
}
