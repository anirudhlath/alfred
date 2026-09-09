import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "@/lib/api";
import { failureText } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import type { NotificationEvent } from "@/lib/types";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set this queue never drains on its own.";
const IDLE_NOTE =
  "Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.";

export interface HeldBackSheetProps {
  open: boolean;
  onClose: () => void;
}

export function HeldBackSheet({ open, onClose }: HeldBackSheetProps) {
  const [queuedAt, setQueuedAt] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // One visit, one drain. The sheet stays mounted while closed (only the read
  // is gated on `open`), so a `Queued` latch from the last visit would still be
  // there on the next — over a note about a refresh that has long since
  // happened, and with no way to try again. Adjusted during render, as
  // `usePresence` does, and on the opening edge rather than the closing one so
  // the leave animation does not flash the idle button back.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setQueuedAt(null);
      setError(null);
    }
  }

  const deferred = useQuery<{ notifications: NotificationEvent[] }>({
    queryKey: ["deferred"],
    queryFn: () => api<{ notifications: NotificationEvent[] }>("/api/admin/notifications/deferred"),
    // Nothing is read until the sheet is actually opened.
    enabled: open,
  });

  const notifications = deferred.data?.notifications ?? [];

  async function drain(): Promise<void> {
    setError(null);
    try {
      await post("/api/admin/notifications/drain");
      setQueuedAt(hhmm(new Date()));
    } catch (caught) {
      setError(failureText(caught));
    }
  }

  return (
    <Sheet open={open} title="Held back" onClose={onClose}>
      <p className="text-[13.5px] leading-[1.5]" style={{ color: "var(--fg2)" }}>
        {INTRO}
      </p>

      {notifications.map((notification) => (
        <div
          key={notification.notification_id}
          className="flex gap-3 py-3"
          style={{ borderTop: "1px solid var(--line)" }}
        >
          <span
            aria-hidden="true"
            className="mt-1.5 h-2 w-2 shrink-0 rounded-[2px]"
            style={{ background: "oklch(0.62 0.11 255)" }}
          />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="t-row">{notification.title}</div>
            <div className="t-meta">
              {notification.urgency} · {hhmm(notification.timestamp)} · deferred by DND
            </div>
          </div>
        </div>
      ))}

      {deferred.isSuccess && notifications.length === 0 ? (
        <div className="t-row" style={{ color: "var(--muted)" }}>
          Nothing is being held back.
        </div>
      ) : null}

      {/* A queue that could not be read must not look like an empty one, or a
          loading one, in the one sheet that exists to make it visible. */}
      {deferred.isError ? (
        <div role="status" className="t-meta text-center">
          {failureText(deferred.error)}
        </div>
      ) : null}

      {notifications.length > 0 ? (
        <>
          <button
            type="button"
            onClick={() => void drain()}
            disabled={queuedAt !== null}
            className="mt-1.5 h-[50px] rounded-[25px] border bg-transparent text-[15px] font-medium disabled:opacity-60"
            style={{ borderColor: "var(--line)", color: "var(--fg)" }}
          >
            {queuedAt ? "Queued" : "Drain queue now"}
          </button>
          <div role="status" className="t-meta text-center">
            {error ??
              (queuedAt
                ? `Accepted at ${queuedAt}. Queue will empty on the next refresh if delivery succeeded.`
                : IDLE_NOTE)}
          </div>
        </>
      ) : null}
    </Sheet>
  );
}
