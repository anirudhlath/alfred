import { cn } from "@/lib/utils";
import type { ChatMessage } from "./use-chat";

export function MessageItem({ message }: { message: ChatMessage }) {
  if (message.role === "user") {
    return (
      <div className="max-w-[70%] self-end rounded-xl rounded-br-sm bg-secondary px-3.5 py-2 text-sm">
        {message.text}
      </div>
    );
  }
  if (message.role === "system") {
    return <div className="self-center font-mono text-xs text-bad">{message.text}</div>;
  }
  return (
    <div className="max-w-[78%] self-start border-l-2 border-reflex pl-3.5">
      <div className="text-sm leading-relaxed text-foreground/90">{message.text}</div>
      {(message.tools?.length || message.latencyMs) && (
        <div className={cn("mt-1.5 flex flex-wrap gap-3 font-mono text-[10px] text-muted-foreground")}>
          {message.tools?.map((t) => <span key={t}>▸ {t}</span>)}
          {message.latencyMs !== undefined && <span>{(message.latencyMs / 1000).toFixed(1)}s</span>}
        </div>
      )}
    </div>
  );
}
