import { Page, StandaloneGate } from "@ds";
import { signedInAt } from "./fixtures";

export const Idle = () => (<Page frame="phone"><StandaloneGate signedInAt={signedInAt} busy={false} onAllow={() => {}} onDismiss={() => {}} /></Page>);
export const Asking = () => (<Page theme="light" frame="phone"><StandaloneGate signedInAt={signedInAt} busy onAllow={() => {}} onDismiss={() => {}} /></Page>);
