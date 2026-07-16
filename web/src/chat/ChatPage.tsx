import { useEffect, useRef } from "react";
import { useChat } from "./use-chat";
import { Composer } from "./Composer";
import { MessageItem } from "./MessageItem";
import { TelemetryRail } from "@/shell/TelemetryRail";

export function ChatPage() {
  const { messages, waiting, sendText, sendAudio } = useChat();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length, waiting]);

  return (
    <div className="flex h-full">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex flex-1 flex-col gap-3.5 overflow-y-auto px-6 py-5">
          {messages.length === 0 && (
            <p className="m-auto font-mono text-xs text-muted-foreground">
              Good evening, sir. How may I be of service?
            </p>
          )}
          {messages.map((m, i) => <MessageItem key={i} message={m} />)}
          {waiting && (
            <div className="flex items-center gap-1.5 self-start border-l-2 border-reflex pl-3.5">
              <span className="pulse-dot bg-reflex text-reflex" />
              <span className="font-mono text-[10px] text-muted-foreground">thinking</span>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
        <Composer onSend={sendText} onAudio={sendAudio} />
      </div>
      <TelemetryRail />
    </div>
  );
}
