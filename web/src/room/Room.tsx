import { useCallback, useEffect, useMemo, useState } from "react";
import { DoorBanner } from "@/door/DoorBanner";
import { DoorLayer } from "@/door/DoorLayer";
import { useDoor } from "@/door/DoorProvider";
import { useActionRoute } from "@/door/useActionRoute";
import { tombstoneItems } from "@/lib/actions";
import { greetingFor, pickHeadline } from "@/lib/headline";
import { pendingActionTitles, toTimelineItems } from "@/lib/history";
import { onVisible } from "@/lib/lifecycle";
import { PresenceSignal } from "@/lib/presence-signal";
import type { StreamRef } from "@/lib/streams";
import { Composer } from "@/room/Composer";
import { DndRow } from "@/room/DndRow";
import { Headline } from "@/room/Headline";
import { HoldToTalk } from "@/room/HoldToTalk";
import { OfflineNote } from "@/room/OfflineNote";
import { PresenceField } from "@/room/PresenceField";
import { StatusLine } from "@/room/StatusLine";
import { Timeline } from "@/room/Timeline";
import { WorkshopHandle } from "@/room/WorkshopHandle";
import { isFirstRun, sessionIdleMs, useOverview } from "@/room/useOverview";
import { useRoom } from "@/room/useRoom";
import { useRoomHistory } from "@/room/useRoomHistory";
import { HeldBackSheet } from "@/sheets/HeldBackSheet";
import { WhySheet } from "@/sheets/WhySheet";
import { useConnection } from "@/shell/ConnectionProvider";
import { ThemeToggle } from "@/shell/ThemeToggle";
import { Workshop } from "@/workshop/Workshop";

export function Room() {
  const { online, chatStatus, lastTrueAt, chat } = useConnection();
  const { data: overview } = useOverview();
  const idleMs = sessionIdleMs(overview);
  const { data: history } = useRoomHistory();
  const door = useDoor();

  // One signal for the app's lifetime: the field reads it every frame and
  // hold-to-talk writes to it, so it must not be rebuilt on a render.
  const [signal] = useState(() => new PresenceSignal());
  const [holding, setHolding] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [workshopOpen, setWorkshopOpen] = useState(false);
  /**
   * The row a `why?` was asked on — the Room's or the Workshop's. Deliberately
   * not cleared when the Workshop closes: the sheet renders over the Room as
   * happily as over the Workshop, and while it is up the Workshop under it is
   * inert, so a close arriving from there is unreachable anyway. The sheet
   * outlives the surface that opened it.
   */
  const [why, setWhy] = useState<StreamRef | null>(null);
  // Stable, so the memoised Workshop does not re-render for every chat frame,
  // overview poll and Door tick the Room re-renders on (Workshop.tsx).
  const closeWorkshop = useCallback(() => setWorkshopOpen(false), []);

  // Read once, refreshed when the app returns — a PWA left open overnight must
  // not still be saying "Good evening".
  const [hour, setHour] = useState(() => new Date().getHours());
  useEffect(() => onVisible(() => setHour(new Date().getHours())), []);

  const historyItems = useMemo(() => (history ? toTimelineItems(history) : undefined), [history]);
  const titles = useMemo(() => pendingActionTitles(history), [history]);
  const { tombstone } = useActionRoute(titles);

  const tombstones = useMemo(() => {
    const items = tombstoneItems(door.actions);
    return tombstone ? [...items, tombstone] : items;
  }, [door.actions, tombstone]);

  const room = useRoom({ history: historyItems, tombstones, idleMs });

  useEffect(() => {
    signal.setThinking(room.thinking);
  }, [signal, room.thinking]);

  // The socket lets a stored session id go at the same boundary the Room windows on.
  useEffect(() => {
    chat.setIdleMs(idleMs);
  }, [chat, idleMs]);

  const firstRun = isFirstRun(overview);
  // "connecting" is the first-ever attempt and "reconnecting" a later one; both
  // read as still trying, which is truer than "Unreachable." for the 200 ms
  // before the socket opens.
  const reconnecting = chatStatus === "connecting" || chatStatus === "reconnecting";
  const dnd = overview?.dnd ?? { active: false };

  const headline = pickHeadline({
    online,
    reconnecting,
    firstRun,
    dnd,
    holding,
    busy: room.thinking,
    hour,
  });

  const banner = door.pending[0] ?? null;

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
        {dnd.active ? (
          <DndRow
            until={dnd.until}
            heldCount={overview?.counts.deferred ?? 0}
            onOpen={() => setSheetOpen(true)}
          />
        ) : null}
      </header>

      <Timeline
        items={room.items}
        firstDayGreeting={firstRun && room.items.length === 0 ? greetingFor(hour) : null}
        onWhy={setWhy}
      />

      {banner ? (
        <DoorBanner
          tracked={banner}
          now={door.now}
          onOpen={() => door.openAction(banner.action.request_id)}
        />
      ) : null}

      <Composer
        online={online}
        onSend={room.sendText}
        hold={
          <HoldToTalk
            signal={signal}
            online={online}
            onHoldingChange={setHolding}
            onAudio={room.sendAudio}
          />
        }
        handle={<WorkshopHandle onOpen={() => setWorkshopOpen(true)} />}
      />

      <HeldBackSheet open={sheetOpen} onClose={() => setSheetOpen(false)} />

      <WhySheet anchor={why} onClose={() => setWhy(null)} />

      <Workshop open={workshopOpen} onClose={closeWorkshop} onWhy={setWhy} />

      <DoorLayer
        tracked={door.current}
        open={door.open}
        online={online}
        now={door.now}
        onClose={door.close}
        onConfirm={door.confirm}
      />
    </main>
  );
}
