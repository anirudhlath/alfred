import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { api, del, type ApiError } from "@/lib/api";
import type { CredentialField, IntegrationInfo } from "@/lib/types";

/** A single credential field row with an optional password visibility toggle. */
function FieldRow({
  name,
  field,
  configured,
  value,
  onChange,
}: {
  name: string;
  field: CredentialField;
  configured: boolean;
  value: string;
  onChange: (value: string) => void;
}) {
  const isPassword = field.field_type === "password";
  const [reveal, setReveal] = useState(false);

  // Ported from master:web/settings.js — for a configured password we mask the
  // placeholder; otherwise fall back to the schema placeholder. Default value is
  // only pre-filled when the field is not already configured.
  const placeholder = configured
    ? isPassword
      ? "••••••••"
      : field.placeholder
    : field.placeholder;

  return (
    <label className="block space-y-1.5">
      <span className="flex items-center gap-2 font-mono text-[11px] tracking-wide text-muted-foreground">
        {field.label}
        {field.transient && (
          <Badge variant="outline" className="text-[9px] text-warn">
            not stored
          </Badge>
        )}
      </span>
      <div className="flex gap-2">
        <Input
          name={name}
          type={isPassword && !reveal ? "password" : "text"}
          placeholder={placeholder}
          value={value}
          required={field.required}
          autoComplete="off"
          onChange={(e) => onChange(e.target.value)}
        />
        {isPassword && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="font-mono text-[10px]"
            aria-pressed={reveal}
            aria-label={reveal ? `Hide ${field.label}` : `Show ${field.label}`}
            onClick={() => setReveal((r) => !r)}
          >
            {reveal ? "HIDE" : "SHOW"}
          </Button>
        )}
      </div>
      {field.help_text && (
        <span className="block text-[11px] text-muted-foreground">{field.help_text}</span>
      )}
    </label>
  );
}

/**
 * Reusable per-integration credential form. Used standalone in Settings and
 * embedded (one per integration) in the onboarding wizard. State is a single
 * `Record<string, string>` of field-name → entered value.
 *
 * `showActions` (default `true`) controls visibility of the CLEAR button only.
 * SAVE and TEST CONNECTION are always shown — they are needed during onboarding.
 * Pass `showActions={false}` from the wizard to hide the destructive CLEAR button
 * that makes no sense mid-onboarding.
 */
export function IntegrationCard({
  integration,
  onSaved,
  showActions = true,
}: {
  integration: IntegrationInfo;
  onSaved?: () => void;
  showActions?: boolean;
}) {
  const { name, category, schema, configured, kind } = integration;
  const fields = schema.fields;

  // Pre-fill defaults only for unconfigured fields (matches old wizard behavior).
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(
      Object.entries(fields).map(([f, field]) => [
        f,
        !configured[f] && field.default ? field.default : "",
      ]),
    ),
  );

  const allConfigured = Object.entries(fields)
    .filter(([, field]) => field.required && !field.transient)
    .every(([f]) => configured[f]);

  // Build the PUT body: only send fields the user actually entered (non-empty),
  // exactly as master:web/settings.js did. Empty fields are left untouched so a
  // configured secret is never clobbered by a blank submit.
  const buildBody = (): Record<string, string> => {
    const body: Record<string, string> = {};
    for (const [f, v] of Object.entries(values)) {
      if (v) body[f] = v;
    }
    return body;
  };

  const save = useMutation({
    mutationFn: () =>
      api(`/api/integrations/${name}/credentials`, {
        method: "PUT",
        body: JSON.stringify(buildBody()),
      }),
    onSuccess: () => {
      toast.success(`${name} credentials saved`);
      onSaved?.();
    },
    onError: (e: ApiError) => toast.error(e.message),
  });

  const clear = useMutation({
    mutationFn: () => del(`/api/integrations/${name}/credentials`),
    onSuccess: () => {
      toast.success(`${name} credentials cleared`);
      setValues((prev) => Object.fromEntries(Object.keys(prev).map((k) => [k, ""])));
      onSaved?.();
    },
    onError: (e: ApiError) => toast.error(e.message),
  });

  const test = useMutation({
    mutationFn: () => api<{ name: string; healthy: boolean }>(`/api/integrations/${name}/status`),
    onSuccess: (r) =>
      r.healthy ? toast.success(`${name}: connection healthy`) : toast.error(`${name}: unhealthy`),
    onError: (e: ApiError) => toast.error(e.message),
  });

  return (
    <Card className="bg-card">
      <CardHeader className="flex-row items-center justify-between gap-2">
        <div>
          <CardTitle className="font-mono text-xs tracking-widest">
            {name.replace(/_/g, " ").toUpperCase()}
          </CardTitle>
          <span className="flex items-center gap-2 font-mono text-[10px] text-muted-foreground">
            {category}
            {kind === "service" && (
              <Badge variant="outline" className="text-[9px]">
                external service
              </Badge>
            )}
          </span>
        </div>
        <Badge variant="outline" className={allConfigured ? "text-ok" : "text-muted-foreground"}>
          {allConfigured ? "configured" : "not configured"}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        {Object.entries(fields).map(([f, field]) => (
          <FieldRow
            key={f}
            name={f}
            field={field}
            configured={configured[f] ?? false}
            value={values[f] ?? ""}
            onChange={(v) => setValues((prev) => ({ ...prev, [f]: v }))}
          />
        ))}
        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            size="sm"
            className="font-mono text-[10px]"
            disabled={save.isPending}
            onClick={() => save.mutate()}
          >
            SAVE
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="font-mono text-[10px]"
            disabled={test.isPending}
            onClick={() => test.mutate()}
          >
            TEST CONNECTION
          </Button>
          {showActions && (
            <Button
              size="sm"
              variant="outline"
              className="font-mono text-[10px] text-bad"
              disabled={clear.isPending}
              onClick={() => clear.mutate()}
            >
              CLEAR
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
