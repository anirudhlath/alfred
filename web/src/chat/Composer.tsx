import { useState } from "react";
import { Textarea } from "@/components/ui/textarea";
import { VoiceButton } from "./VoiceButton";

export function Composer({ onSend, onAudio }: { onSend: (t: string) => void; onAudio: (d: string) => void }) {
  const [text, setText] = useState("");
  const submit = () => {
    const trimmed = text.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setText("");
  };
  return (
    <div className="flex items-end gap-2 border-t border-border p-4">
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          // isComposing guards against submitting while an IME candidate is being
          // confirmed (CJK input) — Enter there commits the candidate, not the message.
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder="Message Alfred…"
        rows={1}
        className="max-h-40 min-h-10 resize-none bg-card"
      />
      <VoiceButton onAudio={onAudio} />
    </div>
  );
}
