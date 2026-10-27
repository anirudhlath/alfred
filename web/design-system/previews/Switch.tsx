import { Page, Switch } from "@ds";

const row = { display: "flex", gap: 16, alignItems: "center" };
export const States = () => (
  <Page>
    <div style={row}>
      <Switch on label="On" inert={false} busy={false} describedBy={undefined} onToggle={() => {}} />
      <Switch on={false} label="Off" inert={false} busy={false} describedBy={undefined} onToggle={() => {}} />
      <Switch on label="Waiting" inert busy describedBy={undefined} onToggle={() => {}} />
    </div>
  </Page>
);
export const Light = () => (
  <Page theme="light">
    <div style={row}>
      <Switch on label="On" inert={false} busy={false} describedBy={undefined} onToggle={() => {}} />
      <Switch on={false} label="Off" inert={false} busy={false} describedBy={undefined} onToggle={() => {}} />
      <Switch on={false} label="Unreadable" inert busy={false} describedBy={undefined} onToggle={() => {}} />
    </div>
  </Page>
);
