import { Page, OfflineNote } from "@ds";
import { lastTrue } from "./fixtures";

export const Offline = () => (<Page><OfflineNote online={false} reconnecting={false} lastTrueAt={lastTrue} /></Page>);
export const Reconnecting = () => (<Page theme="light"><OfflineNote online={false} reconnecting lastTrueAt={lastTrue} /></Page>);
