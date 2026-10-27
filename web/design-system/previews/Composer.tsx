import { Page, Composer, HoldToTalk, WorkshopHandle, PresenceSignal } from "@ds";

const signal = new PresenceSignal();
const hold = (online: boolean) => (
  <HoldToTalk signal={signal} online={online} onHoldingChange={() => {}} onAudio={() => {}} />
);

export const Online = () => (
  <Page padding={0}><Composer online onSend={() => {}} hold={hold(true)} handle={<WorkshopHandle onOpen={() => {}} />} /></Page>
);
export const Offline = () => (
  <Page theme="light" padding={0}><Composer online={false} onSend={() => {}} hold={hold(false)} handle={<WorkshopHandle onOpen={() => {}} />} /></Page>
);
