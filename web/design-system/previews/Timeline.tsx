import { Page, Timeline } from "@ds";
import { items } from "./fixtures";

const box = { height: 620, display: "flex", flexDirection: "column" as const };
export const Thread = () => (<Page padding={0}><div style={box}><Timeline items={items} firstDayGreeting={null} onWhy={() => {}} /></div></Page>);
export const FirstDay = () => (<Page theme="light" padding={0}><div style={box}><Timeline items={[]} firstDayGreeting="Good morning, sir." /></div></Page>);
