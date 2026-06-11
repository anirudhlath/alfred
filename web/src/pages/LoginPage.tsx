import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { loginPasskey } from "@/lib/webauthn";

function friendlyError(e: unknown): string {
  if (e instanceof DOMException && (e.name === "NotAllowedError" || e.name === "AbortError")) {
    return "Passkey prompt was dismissed";
  }
  return e instanceof Error ? e.message : "Sign-in failed";
}

export function LoginPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const conditionalAbort = useRef<AbortController | null>(null);

  const signIn = async (conditional = false) => {
    if (!conditional) conditionalAbort.current?.abort(); // supersede pending autofill
    try {
      const signal = conditional
        ? (conditionalAbort.current = new AbortController()).signal
        : undefined;
      await loginPasskey(conditional, signal);
      // Invalidate the cached auth-status so Guarded refetches rather than
      // serving the stale unauthenticated value, which would cause a redirect loop.
      await queryClient.invalidateQueries({ queryKey: ["auth-status"] });
      navigate("/", { replace: true });
    } catch (e) {
      if (!conditional) setError(friendlyError(e));
    }
  };

  useEffect(() => {
    // Conditional UI: passkey autofill prompt without a click.
    if (
      typeof window.PublicKeyCredential !== "undefined" &&
      typeof PublicKeyCredential.isConditionalMediationAvailable === "function"
    ) {
      void PublicKeyCredential.isConditionalMediationAvailable().then((ok) => {
        if (ok) void signIn(true);
      });
    }
    const ctrl = conditionalAbort;
    return () => ctrl.current?.abort(); // StrictMode + real-unmount cleanup
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="flex h-dvh flex-col items-center justify-center gap-6 bg-background">
      <div className="flex size-14 items-center justify-center rounded-2xl border border-secondary font-mono text-2xl text-primary">
        ◆
      </div>
      <div className="text-center">
        <h1 className="font-mono text-sm tracking-[0.3em] text-foreground">ALFRED</h1>
        <p className="mt-1 text-xs text-muted-foreground">Authentication required</p>
      </div>
      <Button onClick={() => void signIn()} className="font-mono">
        Sign in with passkey
      </Button>
      {error && <p className="font-mono text-xs text-bad">{error}</p>}
    </div>
  );
}
