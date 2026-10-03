import { Page, Divider } from "@ds";
import { itemOf } from "./fixtures";

// Labels from `withDividers`: a day divider, then the idle-gap one.
const day = itemOf("divider", 0);
const gap = itemOf("divider", 1);
export const Day = () => (<Page><Divider label={day.label} /></Page>);
export const NewConversation = () => (<Page theme="light"><Divider label={gap.label} /></Page>);
