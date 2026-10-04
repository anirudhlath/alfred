import { Page, StreamChips } from "@ds";
import { counts } from "./fixtures";

export const AllStreams = () => (<Page padding={16}><StreamChips counts={counts} solo={null} onSolo={() => {}} /></Page>);
export const Soloed = () => (<Page theme="light" padding={16}><StreamChips counts={counts} solo="reflex_observations" onSolo={() => {}} /></Page>);
