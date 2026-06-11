import { Outlet } from "react-router-dom";
import { IconRail } from "./IconRail";
import { TopBar } from "./TopBar";
import { Toaster } from "@/components/ui/sonner";
import { CommandPalette } from "./CommandPalette";

export function AppShell() {
  return (
    <div className="flex h-dvh">
      <IconRail />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main className="min-h-0 flex-1"><Outlet /></main>
      </div>
      <Toaster position="top-right" />
      <CommandPalette />
    </div>
  );
}
