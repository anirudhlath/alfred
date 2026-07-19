import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { success: vi.fn(), error: vi.fn() }),
}));
vi.mock("@/lib/api", () => ({ post: vi.fn() }));

import { toast } from "sonner";
import { post } from "@/lib/api";
import type { ChatServerMessage } from "./types";
import { showNotificationToast } from "./notifications";

type NotificationMessage = Extract<ChatServerMessage, { type: "notification" }>;

function notif(overrides: Partial<NotificationMessage> = {}): NotificationMessage {
  return {
    type: "notification",
    title: "Confirmation required",
    body: "Alfred wants to run 'home.unlock_door' — confirm?",
    urgency: "urgent",
    notification_id: "n-1",
    ...overrides,
  };
}

type ToastOptions = {
  description?: string;
  duration?: number;
  action?: { label: string; onClick: () => void };
};

function lastToastOptions(): ToastOptions {
  const calls = vi.mocked(toast).mock.calls;
  return (calls[calls.length - 1]?.[1] ?? {}) as ToastOptions;
}

describe("showNotificationToast", () => {
  beforeEach(() => vi.clearAllMocks());

  it("renders a plain toast without a Confirm action when no metadata", () => {
    showNotificationToast(notif({ urgency: "important" }));
    expect(toast).toHaveBeenCalledWith("Confirmation required", expect.any(Object));
    expect(lastToastOptions().action).toBeUndefined();
  });

  it("keeps the 10s duration for urgent notifications", () => {
    showNotificationToast(notif());
    expect(lastToastOptions().duration).toBe(10000);
  });

  it("adds a Confirm action when metadata.pending_action_id is present", () => {
    showNotificationToast(notif({ metadata: { pending_action_id: "req-42" } }));
    expect(lastToastOptions().action?.label).toBe("Confirm");
  });

  it("Confirm click POSTs the confirm endpoint and reports success", async () => {
    vi.mocked(post).mockResolvedValue({ status: "confirmed" });
    showNotificationToast(notif({ metadata: { pending_action_id: "req-42" } }));

    lastToastOptions().action?.onClick();
    await vi.waitFor(() => expect(toast.success).toHaveBeenCalled());
    expect(post).toHaveBeenCalledWith("/api/actions/req-42/confirm");
  });

  it("Confirm click reports an error when the action expired", async () => {
    vi.mocked(post).mockRejectedValue(new Error("Pending action not found or expired"));
    showNotificationToast(notif({ metadata: { pending_action_id: "req-43" } }));

    lastToastOptions().action?.onClick();
    await vi.waitFor(() => expect(toast.error).toHaveBeenCalled());
  });
});
