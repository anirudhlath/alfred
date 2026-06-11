export type SourceCategory =
  | "reflex" | "conscious" | "memory" | "trigger" | "user" | "home" | "system";

type Ev = Record<string, unknown>;

export function categorize(stream: string, event: Ev): SourceCategory {
  switch (stream) {
    case "reflex_observations": return "reflex";
    case "user_requests": return "user";
    case "user_responses": return "conscious";
    case "notifications": return "trigger";
    case "home_state":
    case "home_action_results": return "home";
  }
  const type = String(event.event_type ?? "");
  if (type.startsWith("trigger_")) return "trigger";
  if (type === "state_changed") return "home";
  if (type === "action_request" || type === "action_result") {
    const source = String(event.source ?? "");
    if (source.includes("reflex")) return "reflex";
    if (source.includes("trigger")) return "trigger";
    return "conscious";
  }
  return "system";
}

export const CATEGORY_CLASS: Record<SourceCategory, string> = {
  reflex: "text-reflex",
  conscious: "text-conscious",
  memory: "text-memory",
  trigger: "text-trigger",
  user: "text-user",
  home: "text-home",
  system: "text-muted-foreground",
};

export function summarize(stream: string, event: Ev): string {
  const type = String(event.event_type ?? "");
  if (type === "state_changed") return `${event.entity_id} → ${event.new_state}`;
  if (type === "action_request" || type === "action_result")
    return String(event.tool_name ?? type);
  if (type === "trigger_fired") return `${event.trigger_name} fired`;
  if (type === "user_request") return String(event.content ?? "").slice(0, 60);
  if (type === "alfred_response") return String(event.text ?? "").slice(0, 60);
  if (type === "reflex_observation") {
    const action = event.action as Ev | undefined;
    return String(action?.tool_name ?? "observation");
  }
  if (stream === "notifications") return String(event.title ?? "notification");
  return type || "event";
}

export function timeOf(streamId: string): string {
  const ms = Number(streamId.split("-")[0]);
  return new Date(ms).toLocaleTimeString("en-GB", { hour12: false });
}
