import { useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { VoiceButton } from "@/chat/VoiceButton";
import { api } from "@/lib/api";

const SAMPLES_NEEDED = 3;
const PROMPTS = [
  "Alfred, what's on my calendar for tomorrow morning?",
  "Turn off the lights in the living room, please.",
  "Remind me to take the bread out of the oven.",
];

type Status = "recording" | "submitting" | "enrolled" | "error";

export function VoiceEnrollmentCard() {
  const [samples, setSamples] = useState<string[]>([]);
  const [status, setStatus] = useState<Status>("recording");

  const submit = async (all: string[]) => {
    setStatus("submitting");
    try {
      await api("/api/voice/enroll", {
        method: "POST",
        body: JSON.stringify({ identity: "sir", samples: all }),
      });
      setStatus("enrolled");
    } catch {
      setStatus("error");
      setSamples([]);
    }
  };

  const onAudio = (dataUrl: string) => {
    const next = [...samples, dataUrl];
    setSamples(next);
    if (next.length >= SAMPLES_NEEDED) void submit(next);
  };

  return (
    <Card className="bg-card">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <CardTitle className="font-mono text-xs tracking-widest">VOICE ENROLLMENT</CardTitle>
        <span className="font-mono text-xs text-muted-foreground">
          {samples.length} / {SAMPLES_NEEDED}
        </span>
      </CardHeader>
      <CardContent className="space-y-3 font-mono text-xs text-muted-foreground">
        {status === "enrolled" ? (
          <p className="text-ok">Voiceprint enrolled. Satellites will recognize your voice.</p>
        ) : (
          <>
            <p>
              Record {SAMPLES_NEEDED} samples so Alfred can recognize your voice on satellites.
              Read aloud: &ldquo;{PROMPTS[samples.length] ?? PROMPTS[0]}&rdquo;
            </p>
            <div className="flex items-center gap-2">
              <VoiceButton onAudio={onAudio} />
              {status === "submitting" && <span>Enrolling…</span>}
              {status === "error" && <span className="text-bad">Enrollment failed — retry.</span>}
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}
