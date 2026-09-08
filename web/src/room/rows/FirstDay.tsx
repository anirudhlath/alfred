/**
 * The empty Room, on the morning it was installed. The greeting comes from the
 * same hour table the headline uses, so the two agree.
 */
export function FirstDay({ greeting }: { greeting: string }) {
  return (
    <div className="flex flex-1 flex-col justify-center gap-2.5 pb-[60px]">
      <div className="t-alfred">
        {greeting} Nothing has happened yet; I&apos;m watching the house and listening for you.
      </div>
      <div className="t-meta">first run · no memories · 0 routines · hold the button to speak</div>
    </div>
  );
}
