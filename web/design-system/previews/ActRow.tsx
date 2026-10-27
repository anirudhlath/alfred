import { Page, ActRow } from "@ds";
import { itemOf } from "./fixtures";

// Text and meta exactly as the client's history builder writes them.
const reflex = itemOf("act", 0);
const notification = itemOf("act", 1);

export const Reflex = () => (<Page><ActRow hue={reflex.hue} text={reflex.text} meta={reflex.meta} onWhy={() => {}} /></Page>);
export const Notification = () => (<Page theme="light"><ActRow hue={notification.hue} text={notification.text} meta={notification.meta} /></Page>);
