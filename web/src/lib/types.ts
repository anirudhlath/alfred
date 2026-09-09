/** Notification urgency, mirroring core/notifications/schema.py::Urgency. */
export type Urgency = "informational" | "important" | "urgent";

/** Response mood, mirroring bus/schemas/events.py::AlfredResponse.mood. */
export type Mood = "neutral" | "pleased" | "concerned" | "amused" | "serious";

export interface StreamSummary {
  length: number;
  last_id: string | null;
  last_ts: number | null;
  rate_5m: number;
}

/** GET /api/admin/overview. Every key is always present; `cost` may be null. */
export interface Overview {
  redis: { connected: boolean };
  cost: {
    date: string;
    spend_usd: number;
    cap_usd: number;
    alert_sent?: boolean;
    request_count?: number;
    avg_usd?: number;
  } | null;
  dnd: { active: boolean; until?: string | null; reason?: string | null; source?: string };
  counts: { sessions: number; devices: number; deferred: number; triggers: number };
  streams: Record<string, StreamSummary>;
  inference: { ollama: boolean; lmstudio: boolean };
  reflex?: { model: string | null; last_ms: number | null; p50_ms: number | null };
  librarian?: { last_run_at: string | null; reviewed: number | null; next_run_at: string | null };
  /** The chat session's idle timeout as the server reads it (`SESSION_TIMEOUT_MINUTES`),
   *  so the client never hard-codes 30. */
  session: { idle_minutes: number };
}

export interface StreamEntry {
  id: string;
  event: Record<string, unknown>;
}

/** GET /api/admin/streams/{name}?count=&before= */
export interface StreamPage {
  entries: StreamEntry[];
  next_before: string | null;
}

/** A notification as it appears on the `notifications` stream and in the deferred queue. */
export interface NotificationEvent {
  notification_id: string;
  title: string;
  body: string;
  urgency: Urgency;
  source: string;
  timestamp: string;
  metadata?: Record<string, unknown>;
}

export interface AuthStatus {
  registered: boolean;
  authenticated: boolean;
}

export interface CredentialField {
  label: string;
  field_type: "text" | "password" | "url";
  required: boolean;
  placeholder: string;
  default: string;
  help_text: string;
  transient: boolean;
}

export interface IntegrationInfo {
  name: string;
  category: string;
  kind?: "adapter" | "service";
  schema: { fields: Record<string, CredentialField> };
  configured: Record<string, boolean>;
}

/** One domain of the Reflex attention set. `members` may act, `seen` is everything observed. */
export interface AttentionDomain {
  domain: string;
  members: string[];
  seen: string[];
}

/** GET /api/actions/pending and GET /api/actions/{request_id}. */
export interface PendingAction {
  request_id: string;
  tool_name: string;
  target_service: string;
  parameters: Record<string, unknown>;
  reason: string | null;
  source: string;
  timestamp: string;
  ttl_seconds: number;
  expires_at: string;
}

/** An entry on the `home_action_results` stream. */
export interface ActionResultEvent {
  request_id: string;
  tool_name: string;
  status: "success" | "error";
  result?: unknown;
  error?: string | null;
  timestamp?: string;
}

/** Chat WS server→client frames (`/ws`, protocol unchanged). */
export type ChatServerMessage =
  | { type: "session"; session_id: string }
  | { type: "transcription"; text: string; session_id: string }
  | {
      type: "response";
      text: string;
      audio?: string;
      session_id: string;
      actions_taken?: string[];
      mood?: Mood;
    }
  | {
      type: "notification";
      title: string;
      body: string;
      urgency: Urgency;
      notification_id: string;
      audio?: string;
      metadata?: Record<string, unknown>;
    }
  | { type: "error"; text: string; session_id?: string }
  | { type: "pong" };

/** Telemetry WS frames (`/ws/telemetry`). */
export type TelemetryMessage =
  | { type: "subscribed"; streams: string[] }
  | { type: "entry"; stream: string; id: string; event: Record<string, unknown> }
  /** The pump lost Redis (`detail: "redis_error"`); it retries on its own. */
  | { type: "status"; detail: string }
  /** A frame the server could not read. The field is `message` here, `text` on `/ws`. */
  | { type: "error"; message: string }
  | { type: "pong" };
