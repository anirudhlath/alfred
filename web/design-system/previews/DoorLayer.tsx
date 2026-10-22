import { Page, DoorLayer } from "@ds";
import {
  NOW,
  appliedAction,
  declinedAction,
  expiredAction,
  failedAction,
  pendingAction,
  queuedAction,
} from "./fixtures";
import type { TrackedAction } from "@/lib/actions";

const door = (tracked: TrackedAction, theme: "dark" | "light" = "dark", online = true) => () => (
  <Page theme={theme} frame="phone">
    <DoorLayer tracked={tracked} open online={online} now={NOW} onClose={() => {}} onConfirm={() => {}} onDeny={() => {}} />
  </Page>
);
export const Pending = door(pendingAction);
export const PendingLight = door(pendingAction, "light");
export const Queued = door(queuedAction);
export const Applied = door(appliedAction, "light");
export const Failed = door(failedAction);
export const Declined = door(declinedAction, "light");
export const Expired = door(expiredAction);
export const Offline = door(pendingAction, "dark", false);
