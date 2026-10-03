// The Alfred design system: the web client's own components, unmodified, from
// web/src. Everything except Page and RoomScreen is upstream code; those two are
// the canvas's stand-ins for what the app gets from its shell (a theme on <html>,
// a socket feeding the Room).
import "./.cache/ds.css";

// foundation
export { Page } from "./ds/Page";
export { RoomScreen } from "./ds/RoomScreen";
export { PresenceSignal } from "@/lib/presence-signal";
// The stream hues are data the stories and canvas need; this file is a library
// entry, bundled once and never hot-reloaded.
// eslint-disable-next-line react-refresh/only-export-components
export { STREAMS, STREAM_INFO, ring, ringFill, ringText } from "@/lib/streams";

// room
export { Headline } from "@/room/Headline";
export { StatusLine } from "@/room/StatusLine";
export { OfflineNote } from "@/room/OfflineNote";
export { DndRow } from "@/room/DndRow";
export { Composer } from "@/room/Composer";
export { HoldToTalk } from "@/room/HoldToTalk";
export { WorkshopHandle } from "@/room/WorkshopHandle";
export { PresenceField } from "@/room/PresenceField";
export { ThemeToggle } from "@/shell/ThemeToggle";
export { Timeline } from "@/room/Timeline";

// timeline rows
export { YouBubble } from "@/room/rows/YouBubble";
export { AlfredRow } from "@/room/rows/AlfredRow";
export { ActRow } from "@/room/rows/ActRow";
export { Divider } from "@/room/rows/Divider";
export { Tombstone } from "@/room/rows/Tombstone";
export { ThinkingRow } from "@/room/rows/ThinkingRow";
export { TranscribingBubble } from "@/room/rows/TranscribingBubble";
export { FirstDay } from "@/room/rows/FirstDay";

// door
export { FuseRing } from "@/door/FuseRing";
export { DoorBanner } from "@/door/DoorBanner";
export { SlideToConfirm } from "@/door/SlideToConfirm";
export { DoorLayer } from "@/door/DoorLayer";

// gates
export { Gate } from "@/gates/Gate";
export { StepList } from "@/gates/StepList";
export { DeniedGate } from "@/gates/DeniedGate";

// sheets
export { Sheet } from "@/shell/Sheet";

// workshop
export { BenchSwitcher } from "@/workshop/BenchSwitcher";
export { StreamChips } from "@/workshop/StreamChips";
export { EventRow } from "@/workshop/EventRow";
export { Switch } from "@/workshop/Switch";
export { SystemSection, SystemRow } from "@/workshop/SystemFrame";
export { RoutineRow } from "@/workshop/RoutineRow";
export { TriggerRow } from "@/workshop/TriggerRow";
