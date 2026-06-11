import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { api, post, type ApiError } from "@/lib/api";
import { registerPasskey } from "@/lib/webauthn";
import { cn } from "@/lib/utils";
import type { AuthStatus, IntegrationInfo } from "@/lib/types";
import { IntegrationCard } from "./IntegrationCard";

const TOTAL_STEPS = 6;

// Proactivity options — values match OnboardingPayload.proactivity_level.
const PROACTIVITY = [
  {
    value: "opinionated",
    title: "Opinionated",
    blurb: "I'll suggest actions, nudge on routines, and offer my perspective freely.",
  },
  {
    value: "moderate",
    title: "Moderate",
    blurb: "A balanced approach — helpful prompts without being overbearing.",
  },
  {
    value: "conservative",
    title: "Conservative",
    blurb: "I'll wait for explicit requests and keep unsolicited suggestions to a minimum.",
  },
];

// Guest-control values — VERIFIED against master:web/index.html step 3. The
// stored strings are the human labels, not slugs. Lighting + Media default on.
const GUEST_CONTROLS = [
  { value: "Lighting control", label: "Lighting", defaultOn: true },
  { value: "Media playback", label: "Media", defaultOn: true },
  { value: "Climate control", label: "Climate", defaultOn: false },
  { value: "Door locks", label: "Door locks", defaultOn: false },
];

interface OnboardingPayload {
  wake_time?: string;
  work_address?: string;
  dietary_restrictions?: string;
  proactivity_level?: string;
  guest_controls?: string[];
}

export function OnboardingPage() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [step, setStep] = useState(0);

  // Step 0 (passkey)
  const [deviceName, setDeviceName] = useState("");
  const [passkeyError, setPasskeyError] = useState<string | null>(null);

  // Step 1 (personal)
  const [wakeTime, setWakeTime] = useState("07:00");
  const [workAddress, setWorkAddress] = useState("");
  const [dietary, setDietary] = useState("");

  // Step 2 (proactivity)
  const [proactivity, setProactivity] = useState("moderate");

  // Step 3 (guest controls)
  const [guest, setGuest] = useState<string[]>(
    GUEST_CONTROLS.filter((c) => c.defaultOn).map((c) => c.value),
  );

  const { data: authStatus } = useQuery<AuthStatus>({
    queryKey: ["auth-status"],
    queryFn: () => api("/api/auth/status"),
  });

  // Derive the effective step: if the user is already registered and
  // authenticated, skip step 0 (passkey) to avoid InvalidStateError when
  // navigator.credentials.create() is called for an existing credential.
  const alreadySetUp = Boolean(authStatus?.registered && authStatus?.authenticated);
  const activeStep = step === 0 && alreadySetUp ? 1 : step;

  const { data: integrations } = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"],
    queryFn: () => api("/api/integrations"),
  });

  const register = useMutation({
    mutationFn: () => registerPasskey(deviceName.trim() || "This device"),
    onSuccess: async () => {
      setPasskeyError(null);
      // Registration sets the auth cookie server-side; refresh cached status.
      await qc.invalidateQueries({ queryKey: ["auth-status"] });
      setStep(1);
    },
    onError: (e: ApiError) => setPasskeyError(e.message || "Passkey registration failed"),
  });

  const finish = useMutation({
    mutationFn: () => {
      const payload: OnboardingPayload = {
        wake_time: wakeTime,
        work_address: workAddress || undefined,
        dietary_restrictions: dietary || undefined,
        proactivity_level: proactivity,
        guest_controls: guest,
      };
      return post("/api/onboarding", payload);
    },
    onSuccess: async () => {
      // Guarded's auth-status cache may be stale post-onboarding (LoginPage
      // precedent) — invalidate before navigating so it refetches.
      await qc.invalidateQueries({ queryKey: ["auth-status"] });
      navigate("/", { replace: true });
    },
    onError: (e: ApiError) => toast.error(e.message),
  });

  const toggleGuest = (value: string) =>
    setGuest((prev) => (prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]));

  const next = () => setStep(Math.min(activeStep + 1, TOTAL_STEPS - 1));
  const back = () => setStep(Math.max(activeStep - 1, 0));

  return (
    <div className="flex h-dvh items-center justify-center bg-background p-4">
      <Card className="w-full max-w-xl bg-card">
        <CardHeader className="space-y-3">
          <CardTitle className="font-mono text-[11px] tracking-[0.3em] text-muted-foreground">
            STEP {activeStep + 1}/{TOTAL_STEPS}
          </CardTitle>
          <Progress value={((activeStep + 1) / TOTAL_STEPS) * 100} />
        </CardHeader>
        <CardContent className="space-y-5">
          {/* Step 0 — Passkey */}
          {activeStep === 0 && (
            <div className="space-y-4">
              <div>
                <h2 className="text-lg text-foreground">Register your device</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Secure your Alfred instance with a passkey using your device's biometric
                  authentication (Touch ID, Face ID, or Windows Hello).
                </p>
              </div>
              <label className="block space-y-1.5">
                <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
                  Device name
                </span>
                <Input
                  placeholder="e.g. MacBook Pro"
                  value={deviceName}
                  onChange={(e) => setDeviceName(e.target.value)}
                />
              </label>
              {passkeyError && <p className="font-mono text-xs text-bad">{passkeyError}</p>}
              <div className="flex gap-2">
                <Button
                  className="font-mono"
                  disabled={register.isPending}
                  onClick={() => register.mutate()}
                >
                  Register passkey
                </Button>
                {authStatus?.registered && (
                  <Button variant="outline" className="font-mono" onClick={next}>
                    Skip — already registered
                  </Button>
                )}
              </div>
            </div>
          )}

          {/* Step 1 — Personal */}
          {activeStep === 1 && (
            <div className="space-y-4">
              <h2 className="text-lg text-foreground">A few particulars</h2>
              <label className="block space-y-1.5">
                <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
                  What time do you usually wake?
                </span>
                <Input type="time" value={wakeTime} onChange={(e) => setWakeTime(e.target.value)} />
              </label>
              <label className="block space-y-1.5">
                <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
                  Work address
                </span>
                <Input
                  placeholder="123 Main St, City"
                  value={workAddress}
                  onChange={(e) => setWorkAddress(e.target.value)}
                />
              </label>
              <label className="block space-y-1.5">
                <span className="font-mono text-[11px] tracking-wide text-muted-foreground">
                  Dietary restrictions
                </span>
                <Input
                  placeholder="e.g. vegetarian, no shellfish"
                  value={dietary}
                  onChange={(e) => setDietary(e.target.value)}
                />
              </label>
            </div>
          )}

          {/* Step 2 — Proactivity */}
          {activeStep === 2 && (
            <div className="space-y-4">
              <h2 className="text-lg text-foreground">How proactive shall I be?</h2>
              <div className="space-y-2">
                {PROACTIVITY.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setProactivity(opt.value)}
                    className={cn(
                      "block w-full rounded-md border p-3 text-left transition-colors",
                      proactivity === opt.value
                        ? "border-primary bg-primary/10"
                        : "border-border hover:border-muted-foreground",
                    )}
                  >
                    <div className="text-sm text-foreground">{opt.title}</div>
                    <div className="text-xs text-muted-foreground">{opt.blurb}</div>
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Step 3 — Guest mode */}
          {activeStep === 3 && (
            <div className="space-y-4">
              <h2 className="text-lg text-foreground">Guest access</h2>
              <p className="text-sm text-muted-foreground">Which controls may guests use?</p>
              <div className="space-y-2">
                {GUEST_CONTROLS.map((c) => (
                  <label
                    key={c.value}
                    className="flex cursor-pointer items-center gap-3 rounded-md border border-border p-3"
                  >
                    <input
                      type="checkbox"
                      checked={guest.includes(c.value)}
                      onChange={() => toggleGuest(c.value)}
                      className="size-4 accent-primary"
                    />
                    <span className="text-sm text-foreground">{c.label}</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Step 4 — Integrations */}
          {activeStep === 4 && (
            <div className="space-y-4">
              <div>
                <h2 className="text-lg text-foreground">Connections</h2>
                <p className="mt-1 text-sm text-muted-foreground">
                  Connect your services for more informed assistance. You can configure these later
                  in Settings — all are optional.
                </p>
                <p className="mt-1 font-mono text-[11px] text-muted-foreground">
                  Credentials are saved per-card with SAVE; unsaved values are not submitted.
                </p>
              </div>
              <div className="max-h-[50vh] space-y-3 overflow-y-auto pr-1">
                {(integrations ?? []).map((integration) => (
                  <IntegrationCard
                    key={integration.name}
                    integration={integration}
                    showActions={false}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Step 5 — Done */}
          {activeStep === 5 && (
            <div className="space-y-4">
              <h2 className="text-lg text-foreground">Very good, sir.</h2>
              <p className="text-sm text-muted-foreground">
                Your preferences have been noted. I shall learn the rest over time through
                observation. Do not hesitate to correct me.
              </p>
            </div>
          )}

          {/* Navigation (hidden on the passkey step, which has its own buttons) */}
          {activeStep > 0 && (
            <div className="flex justify-between pt-2">
              {/* Hide Back when the passkey step was auto-skipped — there is no
                  valid step to return to (step 0 would re-trigger InvalidStateError). */}
              {!(activeStep === 1 && alreadySetUp) && (
                <Button variant="outline" className="font-mono" onClick={back}>
                  Back
                </Button>
              )}
              {activeStep < TOTAL_STEPS - 1 ? (
                <Button className="font-mono" onClick={next}>
                  Continue
                </Button>
              ) : (
                <Button
                  className="font-mono"
                  disabled={finish.isPending}
                  onClick={() => finish.mutate()}
                >
                  Begin
                </Button>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
