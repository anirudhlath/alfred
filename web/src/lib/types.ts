export interface StreamSummary { length: number; last_id: string | null; last_ts: number | null }

export interface Overview {
  redis: { connected: boolean };
  cost: { date: string; spend_usd: number; cap_usd: number; alert_sent?: boolean } | null;
  dnd: { active: boolean; until?: string | null; reason?: string | null; source?: string };
  counts: { sessions: number; devices: number; deferred: number; triggers: number };
  streams: Record<string, StreamSummary>;
  inference: { ollama: boolean; lmstudio: boolean };
}

export interface StreamEntry { id: string; event: Record<string, unknown> }
export interface StreamPage { entries: StreamEntry[]; next_before: string | null }

export interface EpisodicEntry {
  store: "hot" | "cold";
  score?: number;
  [key: string]: unknown; // defensive — hash fields vary (content, summary, significance, ...)
}
export interface SemanticFile { name: string; dir: string; content: string; modified: string }
export interface Routine {
  name: string; trigger_pattern: string; confidence: number;
  state: "candidate" | "active" | "dormant" | "archived";
  steps: { description: string }[]; last_hit: string | null;
}

export interface Trigger {
  trigger_id: string; trigger_type: string; name: string; enabled: boolean;
  one_shot: boolean; created_by?: string; created_at?: string; last_fired?: string | null;
  urgency?: string; action?: { tool_name: string; target_service: string } | null;
  [key: string]: unknown;
}

export interface DeferredNotification {
  notification_id: string; title: string; body: string; urgency: string;
  source: string; timestamp: string;
}
export interface SessionInfo {
  session_id: string; channel: string; created_at?: string; turns: number; ttl_seconds: number;
}
export interface DeviceInfo { device_token: string; platform?: string; identity?: string; registered_at?: string }

export interface CredentialField {
  label: string; field_type: "text" | "password" | "url"; required: boolean;
  placeholder: string; default: string; help_text: string; transient: boolean;
}
export interface IntegrationInfo {
  name: string; category: string;
  schema: { fields: Record<string, CredentialField> };
  configured: Record<string, boolean>;
}

export interface AuthStatus { registered: boolean; authenticated: boolean }

/** Chat WS server→client messages (existing /ws protocol — unchanged) */
export type ChatServerMessage =
  | { type: "session"; session_id: string }
  | { type: "transcription"; text: string; session_id: string }
  | { type: "response"; text: string; audio?: string; session_id: string;
      actions_taken?: string[]; mood?: string }
  | { type: "notification"; title: string; body: string; urgency: string;
      notification_id: string; audio?: string }
  | { type: "error"; text: string; session_id?: string };

/** Telemetry WS messages (Step 1 protocol) */
export type TelemetryMessage =
  | { type: "subscribed"; streams: string[] }
  | { type: "entry"; stream: string; id: string; event: Record<string, unknown> }
  | { type: "status"; detail: string };
