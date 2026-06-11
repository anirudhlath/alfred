import { Mic } from "lucide-react";
import { Button } from "@/components/ui/button";

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function VoiceButton({ onAudio: _onAudio }: { onAudio: (d: string) => void }) {
  return (
    <Button variant="ghost" size="icon" disabled aria-label="Voice input (unavailable)">
      <Mic className="h-4 w-4" />
    </Button>
  );
}
