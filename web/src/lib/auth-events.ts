/**
 * Two facts the app must react to from anywhere: the session lapsed (401), and
 * the house refused this network (403). A module-level emitter rather than
 * context, because `api()` is a plain function with no React above it.
 */
export type AuthEventKind = "expired" | "denied";

type Handler = () => void;

class AuthEvents {
  private handlers = new Map<AuthEventKind, Set<Handler>>();

  /** Returns the unsubscribe function — safe to use directly as an effect cleanup. */
  on(kind: AuthEventKind, fn: Handler): () => void {
    const existing = this.handlers.get(kind) ?? new Set<Handler>();
    existing.add(fn);
    this.handlers.set(kind, existing);
    return () => {
      existing.delete(fn);
    };
  }

  emit(kind: AuthEventKind): void {
    // Copy first: a handler is allowed to unsubscribe itself while we iterate.
    for (const fn of [...(this.handlers.get(kind) ?? [])]) fn();
  }
}

export const authEvents = new AuthEvents();
