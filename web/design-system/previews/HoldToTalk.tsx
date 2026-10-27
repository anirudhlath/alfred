import { Page, HoldToTalk, PresenceSignal } from "@ds";

const signal = new PresenceSignal();
export const Ready = () => (<Page><HoldToTalk signal={signal} online onHoldingChange={() => {}} onAudio={() => {}} /></Page>);
export const Offline = () => (<Page theme="light"><HoldToTalk signal={signal} online={false} onHoldingChange={() => {}} onAudio={() => {}} /></Page>);
