import { Page, Headline, StatusLine } from "@ds";
import { overview, lastTrue } from "./fixtures";

export const Dark = () => (
  <Page theme="dark">
    <Headline text="Listening, sir." />
    <StatusLine overview={overview} online lastTrueAt={lastTrue} />
  </Page>
);
export const Light = () => (
  <Page theme="light">
    <Headline text="Listening, sir." />
    <StatusLine overview={overview} online lastTrueAt={lastTrue} />
  </Page>
);
