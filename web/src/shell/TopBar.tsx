import { useAlfredStatus } from "./AlfredProvider";
import { cn } from "@/lib/utils";

const LABEL: Record<string, { text: string; dot: string }> = {
  online: { text: "ALFRED ONLINE", dot: "bg-ok text-ok" },
  connecting: { text: "CONNECTING", dot: "bg-warn text-warn" },
  reconnecting: { text: "RECONNECTING", dot: "bg-warn text-warn" },
  offline: { text: "OFFLINE", dot: "bg-bad text-bad" },
  unauthorized: { text: "SIGNED OUT", dot: "bg-bad text-bad" },
};

export function TopBar() {
  const { chatStatus, telemetryStatus } = useAlfredStatus();
  const status =
    [chatStatus, telemetryStatus].find((s) => s !== "online") ?? "online";
  const { text, dot } = LABEL[status] ?? LABEL.offline;
  return (
    <header className="flex h-11 items-center gap-2.5 border-b border-border px-4 font-mono text-[11px]">
      <span className={cn("pulse-dot", dot)} />
      <span className="text-muted-foreground">{text}</span>
      <span className="ml-auto rounded border border-border px-2 py-0.5 text-muted-foreground">⌘K command</span>
    </header>
  );
}
