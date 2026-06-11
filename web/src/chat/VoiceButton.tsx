import { useRef, useState } from "react";
import { Mic, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function VoiceButton({ onAudio }: { onAudio: (dataUrl: string) => void }) {
  const [recording, setRecording] = useState(false);
  const [level, setLevel] = useState(0);
  const [error, setError] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const audioCtx = useRef<AudioContext | null>(null);
  const raf = useRef(0);

  const stop = () => {
    recorder.current?.stop();
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
    cancelAnimationFrame(raf.current);
    void audioCtx.current?.close().catch(() => {});
    audioCtx.current = null;
    setRecording(false);
    setLevel(0);
  };

  const start = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
      const chunks: Blob[] = [];
      rec.ondataavailable = (e) => chunks.push(e.data);
      rec.onstop = () => {
        const blob = new Blob(chunks, { type: "audio/webm" });
        const reader = new FileReader();
        reader.onloadend = () => onAudio(reader.result as string);
        reader.readAsDataURL(blob);
      };

      const ctx = new AudioContext();
      audioCtx.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 256;
      ctx.createMediaStreamSource(stream).connect(analyser);
      const data = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        analyser.getByteTimeDomainData(data);
        let peak = 0;
        for (let i = 0; i < data.length; i++) {
          const v = Math.abs((data[i] ?? 128) - 128) / 128;
          if (v > peak) peak = v;
        }
        setLevel(peak);
        raf.current = requestAnimationFrame(tick);
      };
      tick();

      rec.start();
      recorder.current = rec;
      setRecording(true);
      setError(false);
    } catch {
      setError(true);
    }
  };

  return (
    <Button
      variant="outline"
      size="icon"
      title={
        error
          ? "Microphone unavailable"
          : recording
            ? "Stop recording"
            : "Record voice message"
      }
      onClick={() => (recording ? stop() : void start())}
      className={cn(
        "shrink-0",
        recording && "border-reflex text-reflex",
        error && "border-bad text-bad",
      )}
      style={recording ? { boxShadow: `0 0 ${4 + level * 16}px var(--reflex)` } : undefined}
    >
      {recording ? <Square className="size-4" /> : <Mic className="size-4" />}
    </Button>
  );
}
