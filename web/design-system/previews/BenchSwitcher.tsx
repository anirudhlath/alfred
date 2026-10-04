import { useState } from "react";
import { Page, BenchSwitcher } from "@ds";

function Live({ start }: { start: "activity" | "memory" | "triggers" | "system" }) {
  const [bench, setBench] = useState(start);
  return <BenchSwitcher bench={bench} onChange={setBench} idBase="ds" panelId="ds-panel" />;
}
export const Activity = () => (<Page padding={16}><Live start="activity" /></Page>);
export const System = () => (<Page theme="light" padding={16}><Live start="system" /></Page>);
