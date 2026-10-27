import { Page, TranscribingBubble } from "@ds";

const col = { display: "flex", flexDirection: "column" as const };
export const Dark = () => (<Page><div style={col}><TranscribingBubble seconds={2.4} /></div></Page>);
export const Light = () => (<Page theme="light"><div style={col}><TranscribingBubble seconds={6} /></div></Page>);
