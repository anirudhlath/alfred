/**
 * The gap between releasing the button and the server telling you what it heard.
 * Dashed, because it is not yet a message: nothing has been transcribed, and the
 * count of seconds is the only true thing the client knows about it.
 */
export function TranscribingBubble({ seconds }: { seconds: number }) {
  return (
    <div
      className="t-you max-w-[280px] self-end rounded-[16px_16px_4px_16px] border border-dashed px-3.5 py-2.5 italic"
      style={{ borderColor: "var(--line)", color: "var(--muted)" }}
    >
      Transcribing…
      <div className="mt-1 font-mono text-[10.5px] not-italic">
        audio sent · {seconds.toFixed(1)} s · waiting on server
      </div>
    </div>
  );
}
