import { Page, TriggerRow } from "@ds";
import { NOW, triggers } from "./fixtures";

const noop = () => {};
export const List = () => (
  <Page padding={16}>
    <ul role="list" className="m-0 flex list-none flex-col p-0">
      {triggers.map((t) => (
        <TriggerRow key={t.trigger_id} trigger={t} now={NOW} open={false} onToggleOpen={noop} onToggle={noop} onFire={noop} />
      ))}
    </ul>
  </Page>
);
export const Enabling = () => (
  <Page theme="light" padding={16}>
    <ul role="list" className="m-0 flex list-none flex-col p-0">
      <TriggerRow trigger={triggers[2]} now={NOW} pending={{ kind: "enabling", at: NOW - 5000 }} open={false} onToggleOpen={noop} onToggle={noop} onFire={noop} />
    </ul>
  </Page>
);
export const Open = () => (
  <Page padding={16}>
    <ul role="list" className="m-0 flex list-none flex-col p-0">
      <TriggerRow trigger={triggers[0]} now={NOW} open onToggleOpen={noop} onToggle={noop} onFire={noop} />
    </ul>
  </Page>
);
