import { hhmm } from "@/lib/format";

export interface OfflineNoteProps {
  reconnecting: boolean;
  lastTrueAt: Date | null;
}

/**
 * Spec §5.2.2, "live is not last-known". Shown whenever the chat socket is not
 * online, and always carrying the time it last was.
 */
export function OfflineNote({ reconnecting, lastTrueAt }: OfflineNoteProps) {
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";
  const text = reconnecting
    ? `Trying again. Everything below was last true at ${stamp}.`
    : `No connection to the house since ${stamp}. Everything below is last-known. Sending is paused.`;

  return (
    <div
      className="mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
      style={{ background: "var(--surface)" }}
    >
      <span
        aria-hidden="true"
        className="h-2 w-2 shrink-0 rounded-full"
        style={{ background: "var(--muted)" }}
      />
      <span>{text}</span>
    </div>
  );
}
