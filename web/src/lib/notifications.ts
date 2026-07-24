import { toast } from "sonner";
import { post } from "@/lib/api";
import type { ChatServerMessage } from "@/lib/types";

type NotificationMessage = Extract<ChatServerMessage, { type: "notification" }>;

/** Extract a pending-action id from notification metadata, if present. */
export function pendingActionId(msg: NotificationMessage): string | undefined {
  const id = msg.metadata?.["pending_action_id"];
  return typeof id === "string" ? id : undefined;
}

/** POST the critical-action confirmation endpoint. */
export async function confirmPendingAction(id: string): Promise<void> {
  await post(`/api/actions/${encodeURIComponent(id)}/confirm`);
}

/**
 * Render a notification as a sonner toast. Confirmation-flow notifications
 * (metadata.pending_action_id present) gain a Confirm action button.
 */
export function showNotificationToast(msg: NotificationMessage): void {
  const urgent = msg.urgency === "urgent";
  const id = pendingActionId(msg);
  toast(msg.title, {
    description: msg.body,
    ...(urgent ? { duration: 10000 } : {}),
    ...(id
      ? {
          action: {
            label: "Confirm",
            onClick: () => {
              void confirmPendingAction(id)
                .then(() => toast.success("Action confirmed"))
                .catch((err: unknown) =>
                  toast.error(err instanceof Error ? err.message : "Confirmation failed"),
                );
            },
          },
        }
      : {}),
  });
}
