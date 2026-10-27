import { Page, EventRow } from "@ds";
import { feed } from "./fixtures";

export const Feed = () => (
  <Page padding={16}>
    <ul role="list" className="m-0 flex list-none flex-col p-0">
      {feed.map((row) => (
        <EventRow key={row.key} row={row} expanded={false} onToggle={() => {}} onSolo={() => {}} />
      ))}
    </ul>
  </Page>
);
export const Expanded = () => (
  <Page theme="light" padding={16}>
    <ul role="list" className="m-0 flex list-none flex-col p-0">
      <EventRow row={feed[0]} expanded onToggle={() => {}} onSolo={() => {}} onWhy={() => {}} />
    </ul>
  </Page>
);
