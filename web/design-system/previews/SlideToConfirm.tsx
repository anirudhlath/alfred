import { Page, SlideToConfirm } from "@ds";

const ink = { background: "var(--ink)", color: "var(--paper)", padding: 20 };
export const Ready = () => (<Page padding={0}><div style={ink}><SlideToConfirm hint="Slide to approve" disabled={false} onConfirm={() => {}} /></div></Page>);
export const Offline = () => (<Page theme="light" padding={0}><div style={ink}><SlideToConfirm hint="Slide to approve" disabled onConfirm={() => {}} /></div></Page>);
