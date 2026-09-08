import { StatusLine } from "@/room/StatusLine";
import { useOverview } from "@/room/useOverview";
import { useConnection } from "@/shell/ConnectionProvider";
import { ThemeToggle } from "@/shell/ThemeToggle";

/**
 * Phase 1a: the Room's frame — header padding 72/24/0, the headline, the theme
 * toggle and the status line. The presence field, the timeline, the composer and
 * the Door arrive in plan 1b.
 */
export function Room() {
  const { online, lastTrueAt } = useConnection();
  const { data: overview } = useOverview();

  return (
    <main className="relative flex flex-1 flex-col overflow-hidden" style={{ background: "var(--bg)" }}>
      <header className="relative z-10 flex flex-col gap-1 px-6 pt-[72px]">
        <div className="flex items-end justify-between gap-3">
          <h1 className="t-headline">Listening, sir.</h1>
          <ThemeToggle />
        </div>
        <StatusLine overview={overview} online={online} lastTrueAt={lastTrueAt} />
      </header>
    </main>
  );
}
