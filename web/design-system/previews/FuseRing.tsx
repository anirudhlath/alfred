import { Page, FuseRing } from "@ds";

const ink = { background: "var(--ink)", color: "var(--paper)", padding: 24, display: "flex", gap: 24, alignItems: "center" };
export const Sizes = () => (
  <Page padding={0}>
    <div style={ink}>
      <FuseRing percent={84} size={34} danger={false} />
      <FuseRing percent={84} size={168} danger={false}>
        <span className="t-fuse">4:12</span>
      </FuseRing>
    </div>
  </Page>
);
export const UnderThirtySeconds = () => (
  <Page theme="light" padding={0}>
    <div style={ink}>
      <FuseRing percent={7} size={34} danger />
      <FuseRing percent={7} size={168} danger>
        <span className="t-fuse">0:21</span>
      </FuseRing>
    </div>
  </Page>
);
