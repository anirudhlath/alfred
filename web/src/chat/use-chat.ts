import { useEffect, useRef, useState } from "react";
import { useAlfredStatus } from "@/shell/AlfredProvider";

export interface ChatMessage {
  role: "user" | "alfred" | "system";
  text: string;
  tools?: string[];
  latencyMs?: number;
}

export function useChat() {
  const { chat } = useAlfredStatus();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [waiting, setWaiting] = useState(false);
  // Tracks send time for latency display; assumes single-flight — latency is misattributed if a second message is sent before the first response.
  const sentAt = useRef<number>(0);

  useEffect(() => {
    return chat.listen((msg) => {
      switch (msg.type) {
        case "transcription":
          setMessages((m) => [...m, { role: "user", text: msg.text }]);
          break;
        case "response": {
          const latencyMs = sentAt.current ? Date.now() - sentAt.current : undefined;
          setWaiting(false);
          setMessages((m) => [
            ...m,
            { role: "alfred", text: msg.text, tools: msg.actions_taken, latencyMs },
          ]);
          if (msg.audio) void new Audio(`data:audio/wav;base64,${msg.audio}`).play().catch(() => {});
          break;
        }
        // "notification" frames are handled globally in AlfredProvider so they
        // toast on every route, not only while the chat page is mounted.
        case "error":
          setWaiting(false);
          setMessages((m) => [...m, { role: "system", text: msg.text }]);
          break;
      }
    });
  }, [chat]);

  const sendText = (text: string) => {
    sentAt.current = Date.now();
    setMessages((m) => [...m, { role: "user", text }]);
    setWaiting(true);
    if (!chat.sendText(text)) {
      setWaiting(false);
      setMessages((m) => [...m, { role: "system", text: "Not connected — message not sent." }]);
    }
  };

  const sendAudio = (dataUrl: string) => {
    sentAt.current = Date.now();
    setWaiting(true);
    if (!chat.sendAudio(dataUrl)) {
      setWaiting(false);
      setMessages((m) => [...m, { role: "system", text: "Not connected — message not sent." }]);
    }
  };

  return { messages, waiting, sendText, sendAudio };
}
