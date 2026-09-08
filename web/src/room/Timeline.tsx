import { useLayoutEffect, useRef } from "react";
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
    default: {
      // A ninth kind added to TimelineItem fails here at compile time instead
      // of rendering nothing.
      const exhaustive: never = item;
      return exhaustive;
    }
  }
}

export function Timeline({ items, firstDayGreeting }: TimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  // Ref, not state: whether we are following the bottom must not re-render the
  // list, and the scroll handler fires on every frame of a flick.
  const followRef = useRef(true);

  // A layout effect, so an appended row is anchored before it is painted — a
  // plain effect paints it at the old offset and then jumps. Rows are not the
  // only thing that moves the bottom: the webfonts swap in after first paint
  // and grow the content, and the keyboard shrinks the box (§4.5). Both are
  // resizes, so the same anchor re-runs for them while we are following.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    const content = contentRef.current;
    if (!el || !content) return;
    const anchor = () => {
      if (followRef.current) el.scrollTop = el.scrollHeight;
    };
    anchor();
    const observer = new ResizeObserver(anchor);
    observer.observe(el);
    observer.observe(content);
    return () => observer.disconnect();
  }, [items]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight <= SCROLL_ANCHOR_PX;
  };

  return (
    <div
      ref={scrollRef}
      role="log"
      aria-label="Conversation"
      onScroll={onScroll}
      className="relative z-[1] flex flex-1 flex-col overflow-y-auto px-6 pt-[22px] pb-3"
    >
      {/* The observed content: `flex-1` so FirstDay can centre in an empty box. */}
      <div ref={contentRef} className="flex flex-1 flex-col gap-4">
        {items.length === 0 && firstDayGreeting ? <FirstDay greeting={firstDayGreeting} /> : null}
        {items.map((item) => (
          <Row key={item.id} item={item} />
        ))}
      </div>
    </div>
  );
}
