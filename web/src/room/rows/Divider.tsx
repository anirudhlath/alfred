/** `earlier today` · `yesterday` · `4 Sep` · `new conversation · 20:52`. */
export function Divider({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-2.5 py-1.5">
      <div className="h-px flex-1" style={{ background: "var(--line)" }} />
      <div className="t-meta">{label}</div>
      <div className="h-px flex-1" style={{ background: "var(--line)" }} />
    </div>
  );
}
