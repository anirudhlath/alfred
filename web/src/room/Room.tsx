import { useCallback, useEffect, useMemo, useState } from "react";
import { DoorBanner } from "@/door/DoorBanner";
import { DoorLayer } from "@/door/DoorLayer";
import { useDoor } from "@/door/DoorProvider";
import { useActionRoute } from "@/door/useActionRoute";
import { ReachGates } from "@/gates/ReachGates";
import { tombstoneItems } from "@/lib/actions";
import { greetingFor, pickHeadline } from "@/lib/headline";
import { pendingActionTitles, toTimelineItems } from "@/lib/history";
import { onVisible } from "@/lib/lifecycle";
import { PresenceSignal } from "@/lib/presence-signal";
import { rowKey } from "@/lib/streams";
import { applyUpdate } from "@/lib/sw";
import { Composer } from "@/room/Composer";
import { DndRow } from "@/room/DndRow";
import { Headline } from "@/room/Headline";
import { HoldToTalk } from "@/room/HoldToTalk";
import { OfflineNote } from "@/room/OfflineNote";
import { PresenceField } from "@/room/PresenceField";
import { StatusLine } from "@/room/StatusLine";
import { Timeline } from "@/room/Timeline";
import { WorkshopHandle } from "@/room/WorkshopHandle";
import { useDetail } from "@/room/useDetail";
import { isFirstRun, sessionIdleMs, useOverview } from "@/room/useOverview";
import { useReachActRows, useReachPrompts } from "@/room/useReachPrompts";
import { useRoom } from "@/room/useRoom";
import { useRoomHistory } from "@/room/useRoomHistory";
import { HeldBackSheet } from "@/sheets/HeldBackSheet";
import { WhySheet } from "@/sheets/WhySheet";
import { useConnection, useLastTrue } from "@/shell/ConnectionProvider";
import { Desk } from "@/shell/Desk";
import { LayoutContext, useViewportLayout } from "@/shell/layout";
import { ThemeToggle } from "@/shell/ThemeToggle";
import { UpdateNote } from "@/shell/UpdateNote";
import { useUpdateReady } from "@/shell/useUpdateReady";
import { Workshop } from "@/workshop/Workshop";

export function Room() {
  const layout = useViewportLayout();
  const { online, chatStatus, chat } = useConnection();
  const lastTrueAt = useLastTrue();
  const { data: overview } = useOverview();
  const idleMs = sessionIdleMs(overview);
  const { data: history } = useRoomHistory();
  const door = useDoor();
  const updateReady = useUpdateReady();

  // One signal for the app's lifetime: the field reads it every frame and
  // hold-to-talk writes to it, so it must not be rebuilt on a render.
  const [signal] = useState(() => new PresenceSignal());
  const [holding, setHolding] = useState(false);
  const { why, heldOpen, press, openWhy, openHeld, closeWhy, closeHeld } = useDetail();
  const [workshopOpen, setWorkshopOpen] = useState(false);
  // `open` means nothing at width (R1) and nothing there can close it, so a layer
  // left open would rise again on the way back to the phone, taking focus and
  // covering the Room for a reader who only narrowed a window. During render and
  // not in an effect, so no frame is committed with the stale value.
  if (layout !== "phone" && workshopOpen) setWorkshopOpen(false);
  // Stable, as `useDetail`'s are, so the memoised Workshop does not re-render for
  // every chat frame, overview poll and Door tick the Room re-renders on
  // (Workshop.tsx) — an inline arrow would undo that memoisation entirely.
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

  const deviceActs = useReachActRows();
  const prompts = useReachPrompts(door.pending, door.now);
  const room = useRoom({
    history: historyItems,
    tombstones,
    idleMs,
    deviceActs,
    prompt: prompts.row,
  });
  // Memoised, so a Door tick hands the Timeline the same object.
  const { showMe, notNow } = prompts;
  const promptButtons = useMemo(() => ({ onShowMe: showMe, onNotNow: notNow }), [showMe, notNow]);

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
    <LayoutContext.Provider value={layout}>
      <Desk
        detailOpen={heldOpen || why !== null}
        bench={
          <Workshop open={workshopOpen} onClose={closeWorkshop} onWhy={openWhy} onHeld={openHeld} />
        }
        room={
          <main
            className="relative flex flex-1 flex-col overflow-hidden"
            style={{ background: "var(--bg)" }}
          >
            <PresenceField signal={signal} offline={!online} />

            {/* §11's `padding 40 28 0` at width: the phone's 72 is clearing a
                status bar a laptop does not have. */}
            <header
              className={`relative z-[1] flex flex-col gap-1 ${layout === "phone" ? "px-6 pt-[72px]" : "px-7 pt-10"}`}
            >
              <div className="flex items-end justify-between gap-3">
                <Headline text={headline} />
                <ThemeToggle />
              </div>
              <StatusLine overview={overview} online={online} lastTrueAt={lastTrueAt} />
              <OfflineNote online={online} reconnecting={reconnecting} lastTrueAt={lastTrueAt} />
              <UpdateNote ready={updateReady} onReload={applyUpdate} />
              {dnd.active ? (
                <DndRow
                  until={dnd.until}
                  heldCount={overview?.counts.deferred ?? 0}
                  onOpen={openHeld}
                />
              ) : null}
            </header>

            <Timeline
              items={room.items}
              firstDayGreeting={firstRun && room.items.length === 0 ? greetingFor(hour) : null}
              onWhy={openWhy}
              prompt={promptButtons}
              openThread={layout !== "phone" && why ? rowKey(why) : null}
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
              // Only the phone has a Workshop to open: at width it is already up (R1).
              handle={
                layout === "phone" ? (
                  <WorkshopHandle onOpen={() => setWorkshopOpen(true)} />
                ) : undefined
              }
            />

            <HeldBackSheet open={heldOpen} press={press} onClose={closeHeld} />

            <WhySheet anchor={why} press={press} onClose={closeWhy} />

            <DoorLayer
              tracked={door.current}
              open={door.open}
              online={online}
              now={door.now}
              onClose={door.close}
              onConfirm={door.confirm}
              onDeny={door.deny}
            />

            <ReachGates gate={prompts.gate} signedInAt={null} onClose={prompts.closeGate} />
          </main>
        }
      />
    </LayoutContext.Provider>
  );
}
