import { useId, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { rateText } from "@/lib/format";
import { STREAMS, type StreamName, type StreamRef } from "@/lib/streams";
import { buildThread, type Thread } from "@/lib/trace";
import { WhySheet } from "@/sheets/WhySheet";
import { Desk } from "@/shell/Desk";
import { LayoutContext } from "@/shell/layout";
import { ActivityBench } from "@/workshop/ActivityBench";
import { BenchSwitcher, benchTabId } from "@/workshop/BenchSwitcher";
import type { Activity } from "@/workshop/useActivity";
import { counts, deskFeed, items, lastTrue, NOW, overview } from "../previews/fixtures";
import { RoomScreen } from "./RoomScreen";

const noop = () => {};

const open = deskFeed[2];
const anchor: StreamRef = { stream: open.stream, entry: open.entry };

/** What `useActivity` hands the bench at width: a solo that dims, one row open. */
const activity: Activity = {
  rows: deskFeed,
  counts,
  streamLoaded: Object.fromEntries(STREAMS.map((stream) => [stream, true])) as Record<StreamName, boolean>,
  live: true,
  paused: false,
  liveAt: NOW,
  heldCount: 0,
  solo: open.stream,
  setSolo: noop,
  expanded: open.key,
  toggle: noop,
  pause: noop,
  resume: noop,
  cursor: deskFeed[deskFeed.length - 1].entry.id,
  fetchingOlder: false,
  loadOlder: noop,
  loaded: true,
  error: null,
};

/** A cache already holding the open row's thread, fresh, so `WhySheet` reads nothing. */
function seededClient(): QueryClient {
  const client = new QueryClient();
  const others = deskFeed.filter((row) => row !== open);
  const thread: Thread = { nodes: buildThread(anchor, others), searched: STREAMS.length, partial: 0 };
  client.setQueryData(["trace", anchor.stream, anchor.entry.id], thread);
  return client;
}

/**
 * The laptop's composition (handoff §11, web/src/room/Room.tsx): the Room, the
 * Workshop on its Activity bench with one stream soloed and one row open, and that
 * row's causal thread in the third column. The real `Desk`, bench and thread over
 * the sample data, in a frame 800 px tall. Put it in a `<Page padding={0}>` at least
 * 1280 px wide. Its Room is `RoomScreen`, so it keeps the phone's header and handle.
 */
export function DeskScreen() {
  const [client] = useState(seededClient);
  const tabsBase = useId();
  const panelId = useId();

  return (
    <LayoutContext.Provider value="columns">
      <div
        className="relative flex flex-col overflow-hidden"
        style={{ height: 800, minWidth: 1280 }}
      >
        <Desk
          detailOpen
          room={
            <RoomScreen headline="Listening, sir." overview={overview} lastTrueAt={lastTrue} items={items} now={NOW} />
          }
          bench={
            <>
              {/* `Workshop.tsx`'s section and header at width, which cannot render here:
                  `WorkshopPanel` runs four live hooks. If they change, this changes. */}
              <section aria-label="Workshop" className="flex flex-1 flex-col overflow-hidden">
                <header className="flex flex-col gap-3 px-6 pt-10">
                  <div className="flex items-center justify-between">
                    <span className="t-label" style={{ color: "var(--fg2)" }}>
                      Workshop
                    </span>
                    <span className="t-meta-strong">{`live · ${rateText(overview)}`}</span>
                  </div>
                  <div className="max-w-[520px]">
                    <BenchSwitcher bench="activity" onChange={noop} idBase={tabsBase} panelId={panelId} />
                  </div>
                </header>
                <div
                  role="tabpanel"
                  id={panelId}
                  aria-labelledby={benchTabId(tabsBase, "activity")}
                  tabIndex={0}
                  className="flex flex-1 flex-col overflow-hidden"
                >
                  <ActivityBench activity={activity} onWhy={noop} />
                </div>
              </section>
              {/* Inside `Desk`, which is where it finds the column. The Room has it in its
                  `main`, but `RoomScreen` takes no children. */}
              <QueryClientProvider client={client}>
                <WhySheet anchor={anchor} onClose={noop} />
              </QueryClientProvider>
            </>
          }
        />
      </div>
    </LayoutContext.Provider>
  );
}
