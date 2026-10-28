import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, post } from "@/lib/api";
import { failureText } from "@/lib/auth";
import { hhmm, notificationText } from "@/lib/format";
import { ring, STREAM_INFO } from "@/lib/streams";
import type { NotificationEvent } from "@/lib/types";
import { Sheet } from "@/shell/Sheet";

const INTRO =
  "Non-urgent notifications wait here while do-not-disturb is on. Urgent ones still speak. With no expiry set, everything here is sent when you turn do-not-disturb off.";
const IDLE_NOTE =
  "Queued only; the server does not report delivery. Items stay listed until a fresh read confirms.";

export interface HeldBackSheetProps {
  open: boolean;
  onClose: () => void;
}

export function HeldBackSheet({ open, onClose }: HeldBackSheetProps) {
  const [queuedAt, setQueuedAt] = useState<string | null>(null);
  const [draining, setDraining] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // One visit, one drain. The sheet stays mounted while closed (only the read
  // is gated on `open`), so a `Queued` latch from the last visit would still be
  // there on the next — over a note about a refresh that has long since
  // happened, and with no way to try again. Adjusted during render, as
  // `usePresence` does, and on the opening edge rather than the closing one so
  // the leave animation does not flash the idle button back. A drain still in
  // flight is not forgotten: it holds the button until it is answered.
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
    setDraining(true);
    setError(null);
    try {
      await post("/api/admin/notifications/drain");
      setQueuedAt(hhmm(new Date()));
    } catch (caught) {
      setError(failureText(caught));
    } finally {
      setDraining(false);
    }
  }

  const held = draining || queuedAt !== null;

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
            style={{ background: ring(STREAM_INFO.notifications.hue) }}
          />
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <div className="t-row">{notificationText(notification.body, notification.title)}</div>
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
          {/* Held from the press on, so a drain cannot be queued twice: busy
              while its POST is out (`aria-busy`), `Queued` once it is answered.
              Held by `aria-disabled` and a press that does nothing, never
              `disabled`: the press that starts the drain is the one that holds
              the button, and a native `disabled` would throw focus to <body>
              from under it. Dimmed to `--fg2` on the sheet's `--bg`, a pair
              measured in `test/contrast.test.ts`, never by opacity. */}
          <button
            type="button"
            onClick={() => {
              if (!held) void drain();
            }}
            aria-disabled={held ? true : undefined}
            aria-busy={draining ? true : undefined}
            className="mt-1.5 h-[50px] rounded-[25px] border bg-transparent text-[15px] font-medium"
            style={{
              borderColor: "var(--line)",
              color: held ? "var(--fg2)" : "var(--fg)",
            }}
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
