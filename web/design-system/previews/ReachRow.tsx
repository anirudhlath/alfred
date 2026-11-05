import { Page, ReachRow } from "@ds";
import { pendingAction } from "./fixtures";

// Stamped with the waiting Door's time, as the Room's row is.
const at = pendingAction.action.timestamp;
export const Dark = () => (<Page><ReachRow at={at} onShowMe={() => {}} onNotNow={() => {}} /></Page>);
export const Light = () => (<Page theme="light"><ReachRow at={at} onShowMe={() => {}} onNotNow={() => {}} /></Page>);
