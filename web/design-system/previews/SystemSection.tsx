import { Page, SystemSection, SystemRow } from "@ds";

export const Quiet = () => (
  <Page padding={16}>
    <SystemSection title="Quiet">
    <SystemRow>
      <span className="t-row">Do-not-disturb</span>
      <span className="t-meta">off · urgent still speaks regardless</span>
    </SystemRow>
    <SystemRow>
      <span className="t-row">Held back</span>
      <span className="t-meta">2 held ›</span>
    </SystemRow>
    </SystemSection>
  </Page>
);
export const WithAside = () => (
  <Page theme="light" padding={16}>
    <SystemSection title="Health" aside={<span className="t-meta">live · 07:42:48</span>}>
    <SystemRow>
      <span className="t-row">Do-not-disturb</span>
      <span className="t-meta">off · urgent still speaks regardless</span>
    </SystemRow>
    <SystemRow>
      <span className="t-row">Held back</span>
      <span className="t-meta">2 held ›</span>
    </SystemRow>
    </SystemSection>
  </Page>
);
