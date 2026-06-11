import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { timeOf } from "@/lib/format";
import type { FeedEntry } from "@/shell/AlfredProvider";

export function EventInspector({ entry, onClose }: { entry: FeedEntry | null; onClose: () => void }) {
  return (
    <Sheet open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-[480px] overflow-y-auto bg-panel font-mono sm:max-w-[480px]">
        {entry && (
          <>
            <SheetHeader>
              <SheetTitle className="font-mono text-sm">
                {entry.stream} · {timeOf(entry.id)}
              </SheetTitle>
              <SheetDescription className="font-mono text-[10px] text-muted-foreground">
                Raw event payload
              </SheetDescription>
            </SheetHeader>
            <pre className="mt-2 overflow-x-auto rounded-md border border-border bg-background p-4 text-[11px] leading-relaxed text-foreground/90">
              {JSON.stringify(entry.event, null, 2)}
            </pre>
            <p className="mt-2 px-1 text-[10px] text-muted-foreground">id: {entry.id}</p>
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
