import { NavLink } from "react-router-dom";
import { Activity, Brain, Heart, MessageSquare, Settings, Timer } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAlfredStatus } from "./AlfredProvider";

const ITEMS = [
  { to: "/", label: "Chat", icon: MessageSquare },
  { to: "/activity", label: "Activity", icon: Activity },
  { to: "/memory", label: "Memory", icon: Brain },
  { to: "/triggers", label: "Triggers", icon: Timer },
  { to: "/system", label: "Health", icon: Heart },
] as const;

export function IconRail() {
  const { telemetryStatus } = useAlfredStatus();
  return (
    <nav aria-label="Primary" className="flex w-13 flex-col items-center gap-1 border-r border-border bg-panel py-3">
      <div className="mb-3 flex size-8 items-center justify-center rounded-lg border border-secondary text-primary">◆</div>
      {ITEMS.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          title={label}
          className={({ isActive }) =>
            cn(
              "relative flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground",
              isActive && "bg-secondary text-foreground",
            )
          }
        >
          <Icon className="size-4" />
          {label === "Activity" && telemetryStatus === "online" && (
            <span className="pulse-dot absolute top-1.5 right-1.5 text-reflex bg-reflex" />
          )}
        </NavLink>
      ))}
      <NavLink to="/settings" title="Settings" className="mt-auto flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:text-foreground">
        <Settings className="size-4" />
      </NavLink>
    </nav>
  );
}
