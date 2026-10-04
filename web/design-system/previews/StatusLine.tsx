import { Page, StatusLine } from "@ds";
import { firstRunOverview, lastTrue, overview } from "./fixtures";

export const Live = () => (<Page><StatusLine overview={overview} online lastTrueAt={lastTrue} /></Page>);
export const Offline = () => (<Page><StatusLine overview={overview} online={false} lastTrueAt={lastTrue} /></Page>);
export const FirstRun = () => (<Page theme="light"><StatusLine overview={firstRunOverview} online lastTrueAt={lastTrue} /></Page>);
export const NeverReached = () => (<Page theme="light"><StatusLine overview={undefined} online={false} lastTrueAt={null} /></Page>);
