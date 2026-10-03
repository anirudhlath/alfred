import { Page, RoutineRow } from "@ds";
import { routines } from "./fixtures";

export const Rising = () => (<Page padding={16}><ul role="list" className="m-0 flex list-none flex-col p-0"><RoutineRow routine={routines[0]} open={false} onToggle={() => {}} /></ul></Page>);
export const Open = () => (<Page theme="light" padding={16}><ul role="list" className="m-0 flex list-none flex-col p-0"><RoutineRow routine={routines[0]} open onToggle={() => {}} /></ul></Page>);
export const Dormant = () => (<Page padding={16}><ul role="list" className="m-0 flex list-none flex-col p-0"><RoutineRow routine={routines[1]} open={false} onToggle={() => {}} /></ul></Page>);
