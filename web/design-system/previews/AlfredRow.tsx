import { Page, AlfredRow } from "@ds";
import { itemOf } from "./fixtures";

const reply = itemOf("alfred");
export const Reply = () => (<Page><AlfredRow text={reply.text} at={reply.at} mood={reply.mood} actions={reply.actions} /></Page>);
export const NoTools = () => (<Page theme="light"><AlfredRow text={"Good morning, sir.\nNothing needs you yet."} at={reply.at} actions={[]} /></Page>);
export const Failed = () => (<Page><AlfredRow text="I couldn't reach the house just now." at={reply.at} actions={[]} error /></Page>);
