import { hhmm } from "@/lib/format";

export interface OfflineNoteProps {
  online: boolean;
  reconnecting: boolean;
  lastTrueAt: Date | null;
}

/**
 * Spec §5.2.2, "live is not last-known". Shown whenever the chat socket is not
 * online, and always carrying the time it last was. The live region itself
 * stays mounted, empty, while online: VoiceOver can miss a region that is
 * inserted with its text already in it, and the socket dropping is news.
 */
export function OfflineNote({ online, reconnecting, lastTrueAt }: OfflineNoteProps) {
  const stamp = lastTrueAt ? hhmm(lastTrueAt) : "--:--";
  const text = reconnecting
    ? `Trying again. Everything below was last true at ${stamp}.`
    : `No connection to the house since ${stamp}. Everything below is last-known. Sending is paused.`;

  return (
    <div
      role="status"
      className={
        online
          ? undefined
          : "mt-2 flex items-center gap-2 rounded-[10px] px-3 py-2 text-[13px] leading-[1.4]"
      }
      style={online ? undefined : { background: "var(--surface)" }}
    >
      {online ? null : (
        <>
          <span
            aria-hidden="true"
            className="h-2 w-2 shrink-0 rounded-full"
            style={{ background: "var(--muted)" }}
          />
          <span>{text}</span>
        </>
      )}
    </div>
  );
}
