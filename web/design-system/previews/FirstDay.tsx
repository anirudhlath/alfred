import { Page, FirstDay } from "@ds";

export const Morning = () => (<Page><FirstDay greeting="Good morning, sir." /></Page>);
export const Evening = () => (<Page theme="light"><FirstDay greeting="Good evening, sir." /></Page>);
