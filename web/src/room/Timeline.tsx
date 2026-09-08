import { useEffect, useRef } from "react";
import type { TimelineItem } from "@/lib/history";
import { ActRow } from "@/room/rows/ActRow";
import { AlfredRow } from "@/room/rows/AlfredRow";
import { Divider } from "@/room/rows/Divider";
import { FirstDay } from "@/room/rows/FirstDay";
import { ThinkingRow } from "@/room/rows/ThinkingRow";
import { Tombstone } from "@/room/rows/Tombstone";
import { TranscribingBubble } from "@/room/rows/TranscribingBubble";
import { YouBubble } from "@/room/rows/YouBubble";

/**
 * How far from the bottom still counts as "at the bottom". Wide enough that an
 * iOS rubber-band bounce does not read as the user scrolling away, narrow enough
 * that one row of deliberate scrolling does.
 */
const SCROLL_ANCHOR_PX = 120;

export interface TimelineProps {
  items: TimelineItem[];
  /** Non-null only on a first run with nothing in the thread at all. */
  firstDayGreeting: string | null;
}

function Row({ item }: { item: TimelineItem }) {
  switch (item.kind) {
    case "divider":
      return <Divider label={item.label} />;
    case "you":
      return <YouBubble text={item.text} state={item.state} />;
    case "alfred":
      return (
        <AlfredRow
          text={item.text}
          at={item.at}
          mood={item.mood}
          actions={item.actions}
          error={item.error}
        />
      );
    case "act":
      return <ActRow hue={item.hue} text={item.text} meta={item.meta} />;
    case "tombstone":
      return <Tombstone title={item.title} meta={item.meta} />;
    case "transcribing":
      return <TranscribingBubble seconds={item.seconds} />;
    case "thinking":
      return <ThinkingRow detail={item.detail} />;
  }
}

export function Timeline({ items, firstDayGreeting }: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  // Ref, not state: whether we are following the bottom must not re-render the
  // list, and the scroll handler fires on every frame of a flick.
  const followRef = useRef(true);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el || !followRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [items]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_ANCHOR_PX;
  };

  return (
    <div
      ref={scrollRef}
      data-testid="timeline"
      onScroll={onScroll}
      className="relative z-[1] flex flex-1 flex-col gap-4 overflow-y-auto px-6 pt-[22px] pb-3"
    >
      {items.length === 0 && firstDayGreeting ? <FirstDay greeting={firstDayGreeting} /> : null}
      {items.map((item) => (
        <Row key={item.id} item={item} />
      ))}
    </div>
  );
}
