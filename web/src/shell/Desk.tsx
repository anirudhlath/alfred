import { createContext, useContext, useMemo, useState, type ReactNode } from "react";
import { useLayout } from "@/shell/layout";

export interface DeskSlots {
  /** Over the bench, under 1100 px (handoff §11). */
  overBench: HTMLElement | null;
  /** The third column, from 1100 px. */
  detail: HTMLElement | null;
}

// eslint-disable-next-line react-refresh/only-export-components
export const DeskSlotsContext = createContext<DeskSlots>({ overBench: null, detail: null });

/** Where a detail can portal to. Both null until `Desk` has mounted, and with no `Desk`. */
// eslint-disable-next-line react-refresh/only-export-components
export function useDeskSlots(): DeskSlots {
  return useContext(DeskSlotsContext);
}

export interface DeskProps {
  room: ReactNode;
  bench: ReactNode;
  detailOpen: boolean;
}

/**
 * The phone's layers side by side (handoff §11): the Room, the Workshop's bench
 * and a detail, in the columns `.desk` draws in `index.css`.
 *
 * The same tree at every layout, the phone's included, with the CSS deciding what
 * shows. React keeps a node only while its position in the tree holds, and a window
 * dragged across a breakpoint mid-sentence must not rebuild the Room's: the draft
 * and the scroll position are in it, and its hooks hold the socket's listeners.
 *
 * The slots are state rather than refs, so a consumer re-renders once they exist:
 * a ref's node is not there yet on the render that would portal into it.
 *
 * `inert` goes on the bench's body and never on its slot, which holds the sheet.
 * Over the bench the sheet covers the reader's place, so the place behind it goes
 * quiet. In a third column it covers nothing, and J and K must keep working (R10).
 */
export function Desk({ room, bench, detailOpen }: DeskProps) {
  const layout = useLayout();
  const [overBench, setOverBench] = useState<HTMLElement | null>(null);
  const [detail, setDetail] = useState<HTMLElement | null>(null);
  const slots = useMemo(() => ({ overBench, detail }), [overBench, detail]);

  return (
    <DeskSlotsContext.Provider value={slots}>
      <div
        className="desk"
        data-layout={layout}
        data-detail={layout === "columns" && detailOpen ? "" : undefined}
      >
        {room}
        <div className="desk-line" aria-hidden="true" />
        <div ref={setOverBench} className="desk-bench">
          <div className="desk-bench-body" inert={layout === "sheet-over-bench" && detailOpen}>
            {bench}
          </div>
        </div>
        <div className="desk-line" aria-hidden="true" />
        <div ref={setDetail} className="desk-detail" />
      </div>
    </DeskSlotsContext.Provider>
  );
}
