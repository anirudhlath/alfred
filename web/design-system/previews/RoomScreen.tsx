import { Page, RoomScreen } from "@ds";
import { NOW, firstRunOverview, items, lastTrue, overview, pendingAction } from "./fixtures";

export const Conversation = () => (
  <Page theme="dark" frame="phone">
    <RoomScreen headline="One moment, sir." overview={overview} lastTrueAt={lastTrue} items={items} thinking now={NOW} />
  </Page>
);
export const ApprovalWaiting = () => (
  <Page theme="light" frame="phone">
    <RoomScreen headline="Listening, sir." overview={overview} lastTrueAt={lastTrue} items={items.slice(0, -1)} pending={pendingAction} now={NOW} />
  </Page>
);
export const Offline = () => (
  <Page theme="dark" frame="phone">
    <RoomScreen headline="Unreachable." overview={overview} online={false} lastTrueAt={lastTrue} items={items.slice(0, -1)} now={NOW} />
  </Page>
);
export const DoNotDisturb = () => (
  <Page theme="dark" frame="phone">
    <RoomScreen headline="Quiet until 08:30." overview={overview} lastTrueAt={lastTrue} dnd={{ until: new Date(NOW + 2_940_000).toISOString(), heldCount: 2 }} items={items.slice(0, -1)} now={NOW} />
  </Page>
);
export const FirstRun = () => (
  <Page theme="light" frame="phone">
    <RoomScreen headline="Good morning, sir." overview={firstRunOverview} lastTrueAt={lastTrue} items={[]} firstDayGreeting="Good morning, sir." now={NOW} />
  </Page>
);
