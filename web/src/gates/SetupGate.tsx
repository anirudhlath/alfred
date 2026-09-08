import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Gate } from "@/gates/Gate";
import { StepList, type ProgressStep } from "@/gates/StepList";
import { api, ApiError, put } from "@/lib/api";
import { defaultDeviceName, failureText, rememberDevice } from "@/lib/auth";
import { hhmm } from "@/lib/format";
import type { AttentionDomain, IntegrationInfo } from "@/lib/types";
import { registerPasskey } from "@/lib/webauthn";

const HOME_SERVICE = "home-service";

/**
 * "Locks, alarms and the garage are never on this list; those always come to
 * you." Filtered in the client as well as trusted to the backend's risk tiers:
 * an offer to automate a lock is the wrong thing to render, whatever happens next.
 */
const NEVER_AUTOMATIC = new Set(["lock", "alarm_control_panel", "cover"]);

/**
 * `AttentionUpdate` caps `allow` and `ask` at 200 entities (admin_api.py), and a
 * domain's `:seen` set holds every entity that ever changed state — the sensors
 * alone can pass that. Adding is additive, so a long list goes in pages.
 */
const MAX_ENTITIES_PER_PUT = 200;

function pages<T>(list: T[]): T[][] {
  const out: T[][] = [];
  for (let start = 0; start < list.length; start += MAX_ENTITIES_PER_PUT) {
    out.push(list.slice(start, start + MAX_ENTITIES_PER_PUT));
  }
  return out;
}

/** `media_player` → `Media player`. */
function domainLabel(domain: string): string {
  const words = domain.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function progressSteps(step: number, deviceName: string, registeredAt: string | null): ProgressStep[] {
  const labels = [
    `Register this ${deviceName}`,
    "Connect Home Assistant",
    "Choose what the reflex may touch",
  ];
  return labels.map((label, index) => ({
    label,
    meta: index === 0 && registeredAt ? hhmm(registeredAt) : undefined,
    state: index < step ? "done" : index === step ? "current" : "todo",
  }));
}

export interface SetupGateProps {
  /** Called once the last step is answered — AuthGate refetches from here. */
  onDone: () => void;
}

export function SetupGate({ onDone }: SetupGateProps) {
  // Read once, in an initialiser: neither the hostname nor the UA changes while
  // the gate is open, and reading them in render is impure.
  const [deviceName] = useState(() => defaultDeviceName());
  const [hostname] = useState(() => location.hostname);

  const [step, setStep] = useState(0);
  const [busy, setBusy] = useState(false);
  const [footOverride, setFootOverride] = useState<string | null>(null);
  const [registeredAt, setRegisteredAt] = useState<string | null>(null);
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [allowed, setAllowed] = useState<Record<string, boolean>>({});

  // Both reads start as soon as the passkey exists, so step 2's skip decision is
  // already settled by the time the user presses Continue.
  const integrations = useQuery<IntegrationInfo[]>({
    queryKey: ["integrations"],
    queryFn: () => api<IntegrationInfo[]>("/api/integrations"),
    enabled: step >= 1,
    // A failure here is shown in the foot line, not retried behind a dead button.
    retry: false,
  });

  const attention = useQuery<{ domains: AttentionDomain[] }>({
    queryKey: ["attention"],
    queryFn: () => api<{ domains: AttentionDomain[] }>("/api/admin/attention"),
    enabled: step >= 1,
    // 503 is "the store is down", which is a skip, not something to retry at.
    retry: false,
    // `finish()` writes the difference between the toggles and this data. A
    // refetch — the app backgrounded and refocused before Finish — would move
    // the baseline under a choice already made, and a grant could go unwritten.
    staleTime: Infinity,
  });

  const homeService = integrations.data?.find((entry) => entry.name === HOME_SERVICE) ?? null;
  const fields = useMemo(
    () => (homeService ? Object.entries(homeService.schema.fields) : []),
    [homeService],
  );

  const rows = useMemo(
    () => (attention.data?.domains ?? []).filter((row) => !NEVER_AUTOMATIC.has(row.domain)),
    [attention.data],
  );

  // `allowed` holds only the rows the user has touched. Until then a domain the
  // reflex already acts in reads as allowed, and everything else asks.
  const startsAllowed = (row: AttentionDomain): boolean => row.members.length > 0;
  const isAllowed = (row: AttentionDomain): boolean => allowed[row.domain] ?? startsAllowed(row);

  // Nothing to choose between (or nothing to choose from): the step does not exist.
  // Latched: the parent's `onDone` is an inline closure that changes identity
  // when it re-renders, and this must not fire again on that account.
  const skipped = useRef(false);
  useEffect(() => {
    if (step !== 2 || attention.isPending || rows.length > 0 || skipped.current) return;
    skipped.current = true;
    onDone();
  }, [step, attention.isPending, rows.length, onDone]);

  async function register(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    try {
      await registerPasskey(deviceName);
      const at = new Date().toISOString();
      rememberDevice({ name: deviceName, registeredAt: at });
      setRegisteredAt(at);
      setStep(1);
    } catch (error) {
      // 403 = off the house network. api() has already raised the Denied gate over
      // this one; repeating it in the foot would be the same news, twice.
      if (error instanceof ApiError && error.status === 403) return;
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  async function saveCredentials(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    const body: Record<string, string> = {};
    for (const [key, field] of fields) body[key] = credentials[key] ?? field.default;
    try {
      await put(`/api/integrations/${HOME_SERVICE}/credentials`, body);
      setStep(2);
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return;
      if (error instanceof ApiError && error.status === 502) {
        // Stored in the keyring, but the service was unreachable for the push. The
        // credentials are safe and will be re-pushed on the next registration, so
        // this advances — carrying the server's own sentence forward.
        setFootOverride(error.detail);
        setStep(2);
        return;
      }
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  async function finish(): Promise<void> {
    setBusy(true);
    setFootOverride(null);
    try {
      // Only the rows that end up different from how they started are written:
      // a row tapped twice looks untouched, and is.
      for (const row of rows) {
        const allow = isAllowed(row);
        if (allow === startsAllowed(row)) continue;
        // `allow` adds what has been seen; `ask` removes what is a member today —
        // and the removal is sticky, so the YAML seed will not re-add it.
        for (const page of pages(allow ? row.seen : row.members)) {
          await put(`/api/admin/attention/${row.domain}`, allow ? { allow: page } : { ask: page });
        }
      }
      onDone();
    } catch (error) {
      if (error instanceof ApiError && error.status === 403) return;
      setFootOverride(failureText(error));
    } finally {
      setBusy(false);
    }
  }

  function toggleDomain(domain: string): void {
    const row = rows.find((candidate) => candidate.domain === domain);
    if (!row) return;
    setAllowed((current) => ({ ...current, [domain]: !(current[domain] ?? startsAllowed(row)) }));
  }

  if (step === 0) {
    return (
      <Gate
        kicker={`first run · ${hostname}`}
        title="Good evening. I am Alfred."
        body="This device will hold the only key to the house. There is no password anywhere; a passkey on this phone, unlocked by Face ID, is how you get in."
        primary={{ label: "Create passkey with Face ID", onClick: () => void register(), busy }}
        foot={footOverride ?? "The passkey never leaves the phone. Nothing here phones home."}
      >
        <StepList variant="progress" steps={progressSteps(0, deviceName, registeredAt)} />
      </Gate>
    );
  }

  if (step === 1) {
    return (
      <Gate
        kicker="first run · 1 of 3 done"
        title="Registered."
        body="Now the house. Paste a long-lived Home Assistant token; I will read state and, later, act on the devices you allow."
        primary={{
          label: "Continue",
          onClick: () => void saveCredentials(),
          busy,
          disabled: fields.length === 0,
        }}
        secondary={{
          label: "Do this later",
          onClick: () => {
            setFootOverride(null);
            setStep(2);
          },
          // Walking away mid-write would leave the write to land on step 2's foot.
          disabled: busy,
        }}
        foot={
          footOverride ??
          (integrations.isError
            ? failureText(integrations.error)
            : "Stored encrypted at rest on your hardware.")
        }
      >
        <StepList variant="progress" steps={progressSteps(1, deviceName, registeredAt)} />
        <div className="flex flex-col gap-3 pt-2">
          {fields.map(([key, field]) => (
            // The help text sits beside the label, not inside it, so the field's
            // accessible name is exactly `field.label`.
            <div key={key} className="flex flex-col gap-1.5">
              <label className="flex flex-col gap-1.5">
                <span className="t-label">{field.label}</span>
                <input
                  type={
                    field.field_type === "password"
                      ? "password"
                      : field.field_type === "url"
                        ? "url"
                        : "text"
                  }
                  value={credentials[key] ?? field.default}
                  placeholder={field.placeholder}
                  autoComplete="off"
                  autoCapitalize="none"
                  autoCorrect="off"
                  spellCheck={false}
                  aria-describedby={field.help_text ? `${key}-help` : undefined}
                  onChange={(event) =>
                    setCredentials((current) => ({ ...current, [key]: event.target.value }))
                  }
                  className="h-[50px] rounded-[25px] border px-[18px] text-[15px]"
                  style={{
                    background: "var(--field)",
                    borderColor: "var(--line)",
                    color: "var(--fg)",
                  }}
                />
              </label>
              {field.help_text ? (
                <span id={`${key}-help`} className="t-meta">
                  {field.help_text}
                </span>
              ) : null}
            </div>
          ))}
        </div>
      </Gate>
    );
  }

  // Skipping (see the effect above): nothing to draw while the parent takes over,
  // rather than a one-frame flash of an empty step.
  if (!attention.isPending && rows.length === 0) return null;

  return (
    <Gate
      kicker="first run · 2 of 3 done"
      title="What may the reflex touch?"
      body="The small local model reacts in under half a second. It is only allowed low-risk devices you list here. Locks, alarms and the garage are never on this list; those always come to you."
      primary={{
        label: "Finish",
        onClick: () => void finish(),
        busy,
        disabled: attention.isPending,
      }}
      foot={footOverride ?? "Change this any time under Workshop › System."}
    >
      {/* One row per domain the house has emitted — dozens on a real HA, not the
          handful in the fixture — so the list scrolls under the pinned footer
          rather than growing the page (the gate does not rubber-band, §4.6). */}
      <div className="max-h-[40dvh] overflow-y-auto overscroll-contain">
        <StepList
          variant="toggle"
          onToggle={toggleDomain}
          steps={rows.map((row) => ({
            id: row.domain,
            label: `${domainLabel(row.domain)} · ${row.seen.length} found`,
            allowed: isAllowed(row),
          }))}
        />
      </div>
    </Gate>
  );
}
