import { Page, ThemeToggle, Headline } from "@ds";

const Row = () => (
  <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between" }}>
    <Headline text="Listening, sir." />
    <ThemeToggle />
  </div>
);
export const Dark = () => (<Page><Row /></Page>);
export const Light = () => (<Page theme="light"><Row /></Page>);
