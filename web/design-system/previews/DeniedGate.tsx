import { Page, DeniedGate } from "@ds";
import { lastTrue } from "./fixtures";

export const OffNetwork = () => (<Page frame="phone"><DeniedGate lastTrue={lastTrue} onDismiss={() => {}} /></Page>);
export const NeverConnected = () => (<Page theme="light" frame="phone"><DeniedGate onDismiss={() => {}} /></Page>);
