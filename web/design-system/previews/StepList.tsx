import { Page, StepList } from "@ds";

export const Progress = () => (
  <Page>
    <StepList variant="progress" steps={[
      { label: "Passkey", meta: "registered", state: "done" },
      { label: "Home Assistant", state: "current" },
      { label: "What the reflex may touch", state: "todo" },
    ]} />
  </Page>
);
export const Toggle = () => (
  <Page theme="light">
    <StepList variant="toggle" onToggle={() => {}} steps={[
      { id: "light", label: "Lights", allowed: true },
      { id: "media_player", label: "Media players", allowed: true },
      { id: "switch", label: "Fans & plugs", allowed: false },
    ]} />
  </Page>
);
