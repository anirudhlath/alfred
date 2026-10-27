import { Page, Tombstone } from "@ds";
import { tombstones } from "./fixtures";

const [expired, answered] = tombstones;
export const Expired = () => (<Page><Tombstone title={expired.title} meta={expired.meta} /></Page>);
export const Answered = () => (<Page theme="light"><Tombstone title={answered.title} meta={answered.meta} /></Page>);
