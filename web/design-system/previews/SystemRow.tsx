import { Page, SystemSection, SystemRow } from "@ds";

export const Rows = () => (
  <Page padding={16}>
    <SystemSection title="Maintenance">
      <SystemRow>
        <span className="t-row">Nightly consolidation</span>
        <span className="t-meta">last 03:00 · 42 reviewed</span>
      </SystemRow>
    </SystemSection>
  </Page>
);
