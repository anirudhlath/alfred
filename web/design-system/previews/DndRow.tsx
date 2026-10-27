import { Page, DndRow } from "@ds";
import { NOW } from "./fixtures";

export const UntilATime = () => (<Page><DndRow until={new Date(NOW + 2_940_000).toISOString()} heldCount={2} onOpen={() => {}} /></Page>);
export const NoExpiry = () => (<Page theme="light"><DndRow until={null} heldCount={5} onOpen={() => {}} /></Page>);
