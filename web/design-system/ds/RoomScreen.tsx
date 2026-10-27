import { useEffect, useMemo } from "react";
import { DoorBanner } from "@/door/DoorBanner";
import type { TrackedAction } from "@/lib/actions";
import type { TimelineItem } from "@/lib/history";
import { PresenceSignal } from "@/lib/presence-signal";
import type { Overview } from "@/lib/types";
import { Composer } from "@/room/Composer";
import { DndRow } from "@/room/DndRow";
import { Headline } from "@/room/Headline";
import { HoldToTalk } from "@/room/HoldToTalk";
import { OfflineNote } from "@/room/OfflineNote";
import { PresenceField } from "@/room/PresenceField";
import { StatusLine } from "@/room/StatusLine";
import { Timeline } from "@/room/Timeline";
import { WorkshopHandle } from "@/room/WorkshopHandle";
import { ThemeToggle } from "@/shell/ThemeToggle";

export interface RoomScreenProps {
  /** `Listening, sir.` · `Go on, sir.` · `One moment, sir.` · `Quiet until 08:30.` · `Unreachable.` · `Reconnecting…` */
  headline: string;
  overview?: Overview;
  online?: boolean;
  reconnecting?: boolean;
  lastTrueAt?: Date | null;
  /**
   * Shown only while do-not-disturb is on. `until: null` is the queue that
   * waits until do-not-disturb is turned off, which sends it.
   */
  dnd?: { until: string | null; heldCount: number } | null;
  items: TimelineItem[];
  /** Non-null only on a first run with nothing in the thread. */
  firstDayGreeting?: string | null;
  /** Sweeps the presence field, as it does while the conscious mind works. */
  thinking?: boolean;
  /** An approval waiting: draws the Door banner above the composer. */
  pending?: TrackedAction | null;
  /** Epoch ms the fuse is measured against. */
  now?: number;
}

/**
 * The Room, assembled from the app's own parts in the app's own order
 * (web/src/room/Room.tsx): presence field, header, timeline, Door banner,
 * composer. Data comes in as props instead of from the socket. Put it in a
 * `<Page frame="phone">`.
 */
export function RoomScreen({
  headline,
  overview,
  online = true,
  reconnecting = false,
  lastTrueAt = null,
  dnd = null,
  items,
  firstDayGreeting = null,
  thinking = false,
  pending = null,
  now = 0,
}: RoomScreenProps) {
  const signal = useMemo(() => new PresenceSignal(), []);
  useEffect(() => {
    signal.setThinking(thinking);
  }, [signal, thinking]);

  return (
    <main
      className="relative flex flex-1 flex-col overflow-hidden"
      style={{ background: "var(--bg)" }}
    >
      <PresenceField signal={signal} offline={!online} />

      <header className="relative z-[1] flex flex-col gap-1 px-6 pt-[72px]">
        <div className="flex items-end justify-between gap-3">
          <Headline text={headline} />
          <ThemeToggle />
        </div>
        <StatusLine overview={overview} online={online} lastTrueAt={lastTrueAt} />
        <OfflineNote online={online} reconnecting={reconnecting} lastTrueAt={lastTrueAt} />
        {dnd ? <DndRow until={dnd.until} heldCount={dnd.heldCount} onOpen={() => {}} /> : null}
      </header>

      <Timeline items={items} firstDayGreeting={firstDayGreeting} onWhy={() => {}} />

      {pending ? <DoorBanner tracked={pending} now={now} onOpen={() => {}} /> : null}

      <Composer
        online={online}
        onSend={() => {}}
        hold={
          <HoldToTalk
            signal={signal}
            online={online}
            onHoldingChange={() => {}}
            onAudio={() => {}}
          />
        }
        handle={<WorkshopHandle onOpen={() => {}} />}
      />
    </main>
  );
}
