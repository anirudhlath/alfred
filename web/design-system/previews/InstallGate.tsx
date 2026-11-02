import { Page, InstallGate } from "@ds";

export const Dark = () => (<Page frame="phone"><InstallGate onDismiss={() => {}} /></Page>);
export const Light = () => (<Page theme="light" frame="phone"><InstallGate onDismiss={() => {}} /></Page>);
