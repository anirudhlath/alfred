import { Page, Sheet, ActRow } from "@ds";

export const HeldBack = () => (
  <Page frame="phone">
    <Sheet open title="Held back" onClose={() => {}}>
      <p className="t-body" style={{ color: "var(--fg2)" }}>Non-urgent notifications wait here while do-not-disturb is on.</p>
      <ActRow hue={255} text="Your parcel was delivered" meta="important · 07:02 · deferred by DND" />
      <ActRow hue={255} text="Washing machine finished" meta="informational · 07:20 · deferred by DND" />
    </Sheet>
  </Page>
);
export const Light = () => (
  <Page theme="light" frame="phone">
    <Sheet open title="Held back" onClose={() => {}}>
      <p className="t-body" style={{ color: "var(--fg2)" }}>Nothing is waiting.</p>
    </Sheet>
  </Page>
);
