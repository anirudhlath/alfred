export interface YouBubbleProps {
  text: string;
  state: "sent" | "unsent";
}

/**
 * Right-aligned, max 280, radius `16 16 4 16`. Unsent fades and says so. The
 * speaker is visible only to a screen reader: the bubble's side says it to eyes.
 */
export function YouBubble({ text, state }: YouBubbleProps) {
  const unsent = state === "unsent";
  return (
    <div
      className="t-you max-w-[280px] self-end rounded-[16px_16px_4px_16px] border px-3.5 py-2.5"
      style={{ borderColor: "var(--line)", opacity: unsent ? 0.6 : 1 }}
    >
      <span className="sr-only">You: </span>
      {text}
      {unsent ? (
        <div className="mt-1 font-mono text-[10.5px]" style={{ color: "var(--muted)" }}>
          not sent · will retry when connected
        </div>
      ) : null}
    </div>
  );
}
