import { Page, DoorBanner } from "@ds";
import { NOW, dangerAction, pendingAction } from "./fixtures";

export const Waiting = () => (<Page padding={8}><DoorBanner tracked={pendingAction} now={NOW} onOpen={() => {}} /></Page>);
export const AboutToLapse = () => (<Page theme="light" padding={8}><DoorBanner tracked={dangerAction} now={NOW} onOpen={() => {}} /></Page>);
