import { api, post } from "./api";
import type { ActionResultEvent, PendingAction } from "./types";

/**
 * Where one approval stands.
 *
 * `queued` is deliberately not `applied`: `POST /api/actions/{id}/confirm`
 * republishes the action and returns immediately, so a 200 proves only that the
 * house accepted the answer (spec §5.2.1). `applied` comes from the
 * `home_action_results` stream and nowhere else.
 */
export type ActionPhase = "pending" | "queued" | "applied" | "expired" | "answered";

export interface TrackedAction {
  action: PendingAction;
  phase: ActionPhase;
  /** ISO, when the confirm POST succeeded. */
  confirmedAt?: string;
  /** ISO, when the result arrived on the telemetry stream. */
  appliedAt?: string;
  result?: ActionResultEvent;
}

export type ActionEvent =
  | { type: "loaded"; actions: PendingAction[] }
  | { type: "arrived"; action: PendingAction }
  | { type: "confirm-sent"; id: string; at: string }
  | { type: "confirm-404"; id: string }
  | { type: "result"; result: ActionResultEvent; at: string }
  | { type: "tick"; now: number };

/** Seconds left on the server's `expires_at`. Never negative; 0 if unreadable. */
export function fuseRemaining(action: PendingAction, now: number): number {
  const expires = Date.parse(action.expires_at);
  if (Number.isNaN(expires)) return 0;
  return Math.max(0, (expires - now) / 1000);
}

function byAge(a: TrackedAction, b: TrackedAction): number {
  return Date.parse(a.action.timestamp) - Date.parse(b.action.timestamp);
}

export function actionReducer(state: TrackedAction[], ev: ActionEvent): TrackedAction[] {
  switch (ev.type) {
    case "loaded": {
      const known = new Map(state.map((item) => [item.action.request_id, item]));
      const listed = new Set(ev.actions.map((action) => action.request_id));

      const loaded: TrackedAction[] = ev.actions.map((action) => {
        const previous = known.get(action.request_id);
        return previous ? { ...previous, action } : { action, phase: "pending" };
      });

      // Everything we track that the list did not mention, untouched. This read
      // races the deep link's own `GET /api/actions/{id}` on a cold launch, and
      // demoting a `pending` action because an empty list landed second would
      // tombstone a live approval. One consumed elsewhere still resolves: `tick`
      // expires it, a 404 confirm answers it, a result frame applies it.
      const kept: TrackedAction[] = state.filter(
        (item) => !listed.has(item.action.request_id),
      );

      return [...kept, ...loaded].sort(byAge);
    }

    case "arrived": {
      const id = ev.action.request_id;
      if (state.some((item) => item.action.request_id === id)) {
        return state.map((item) =>
          item.action.request_id === id ? { ...item, action: ev.action } : item,
        );
      }
      const arrived: TrackedAction = { action: ev.action, phase: "pending" };
      return [...state, arrived].sort(byAge);
    }

    case "confirm-sent":
      return state.map((item) =>
        item.action.request_id === ev.id
          ? { ...item, phase: "queued", confirmedAt: ev.at }
          : item,
      );

    case "confirm-404":
      return state.map((item) =>
        item.action.request_id === ev.id ? { ...item, phase: "answered" } : item,
      );

    case "result":
      // Applied even from `expired`: if the lock reported, it happened, and the
      // tombstone was wrong.
      return state.map((item) =>
        item.action.request_id === ev.result.request_id
          ? { ...item, phase: "applied", appliedAt: ev.at, result: ev.result }
          : item,
      );

    case "tick": {
      // Identity when nothing changed — this runs once a second and must not
      // re-render the Room for a fuse that merely moved.
      const lapsed = state.some(
        (item) => item.phase === "pending" && fuseRemaining(item.action, ev.now) <= 0,
      );
      if (!lapsed) return state;
      return state.map((item) =>
        item.phase === "pending" && fuseRemaining(item.action, ev.now) <= 0
          ? { ...item, phase: "expired" }
          : item,
      );
    }
  }

  return state;
}

/** `GET /api/actions/pending` — oldest first, per the route's own contract. */
export async function fetchPending(): Promise<PendingAction[]> {
  const body = await api<{ actions?: PendingAction[] }>("/api/actions/pending");
  return body.actions ?? [];
}

/** `GET /api/actions/{id}` — 404 means consumed or expired. Throws `ApiError`. */
export function fetchAction(id: string): Promise<PendingAction> {
  return api<PendingAction>(`/api/actions/${encodeURIComponent(id)}`);
}

/** `POST /api/actions/{id}/confirm` — 200 is `queued`, 404 is already answered. */
export async function confirmAction(id: string): Promise<void> {
  await post<{ status: string }>(`/api/actions/${encodeURIComponent(id)}/confirm`);
}
