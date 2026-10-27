import { Page, YouBubble } from "@ds";

const col = { display: "flex", flexDirection: "column" as const, gap: 16 };
export const Sent = () => (<Page><div style={col}><YouBubble text="Is the back door locked?" state="sent" /></div></Page>);
export const Unsent = () => (<Page theme="light"><div style={col}><YouBubble text="Turn the heating up a notch" state="unsent" /></div></Page>);
