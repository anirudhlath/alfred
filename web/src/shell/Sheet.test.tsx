import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Desk, DeskSlotsContext, type DeskSlots } from "./Desk";
import { LayoutContext, type Layout } from "./layout";
import { Sheet, type SheetProps } from "./Sheet";

const TITLE = "Why Alfred did that";
const EYEBROW = "Causal thread";
const INTRO = "Solid links are joined by an id the server holds.";

let root: HTMLElement;
let app: HTMLElement;
let overBench: HTMLElement;
let detail: HTMLElement;

beforeEach(() => {
  // The slots are inside `#root`, as `Desk`'s are, so a gate's `inert` reaches them.
  root = document.createElement("div");
  root.id = "root";
  app = document.createElement("div");
  overBench = document.createElement("div");
  detail = document.createElement("div");
  root.append(app, overBench, detail);
  document.body.append(root);
});

afterEach(() => {
  root.remove();
});

/** The opener and another place focus can be, around whatever is under test. */
function frame(layout: Layout, slots: DeskSlots, children: ReactNode) {
  return (
    <LayoutContext.Provider value={layout}>
      <DeskSlotsContext.Provider value={slots}>
        <button type="button">Opener</button>
        <button type="button">Elsewhere</button>
        {children}
      </DeskSlotsContext.Provider>
    </LayoutContext.Provider>
  );
}

function tree(layout: Layout, open: boolean, props: Partial<SheetProps> = {}, slots?: DeskSlots) {
  return frame(
    layout,
    slots ?? { overBench, detail },
    <Sheet
      open={open}
      title={TITLE}
      eyebrow={EYEBROW}
      intro={INTRO}
      onClose={() => {}}
      {...props}
    >
      <p>The thread</p>
    </Sheet>,
  );
}

const mount = (ui: ReactNode) => render(ui, { container: app });
const button = (name: string) => screen.getByRole("button", { name });
const escape = (target: Element | Document = document.body) =>
  fireEvent.keyDown(target, { key: "Escape" });
const column = () => screen.getByRole("complementary", { name: TITLE });
const queryColumn = () => screen.queryByRole("complementary", { name: TITLE });
const dialog = () => screen.getByRole("dialog", { name: TITLE });

/**
 * Past a leave, with its focus settled. The leave's timer commits outside `act`, and
 * that commit's passive effects run a task later, so `waitFor` can return between the
 * two and read `<body>`. A render flushes them first.
 */
async function pastLeave(rerender: (ui: ReactNode) => void, ui: ReactNode, name = TITLE) {
  await waitFor(() => expect(screen.queryByRole("dialog", { name })).toBeNull());
  rerender(ui);
}

describe("Sheet on the phone", () => {
  it("phone: a modal dialog on body", () => {
    mount(tree("phone", true));

    expect(dialog()).toHaveAttribute("aria-modal", "true");
    expect(dialog().parentElement).toBe(document.body);
    expect(dialog()).toHaveClass("viewport-fill", "z-20");
    expect(root).toHaveAttribute("inert");
  });

  it("phone: the intro opens the body, and there is no eyebrow", () => {
    mount(tree("phone", true));

    const intro = screen.getByText(INTRO);
    expect(intro).toHaveClass("text-[13.5px]", "leading-[1.5]");
    expect(intro).toHaveStyle({ color: "var(--fg2)" });
    expect(intro.nextElementSibling).toBe(screen.getByText("The thread"));
    expect(screen.queryByText(EYEBROW)).toBeNull();
    expect(screen.queryByRole("button", { name: "esc closes" })).toBeNull();
  });

  it("phone: a new subject leaves the body where it is", () => {
    const { rerender } = mount(tree("phone", true, { subject: "a" }));
    const body = screen.getByText("The thread").parentElement as HTMLElement;
    body.scrollTop = 200;

    rerender(tree("phone", true, { subject: "b" }));
    expect(body.scrollTop).toBe(200);
  });

  it("phone: Escape arrives on the panel, because a gate over it swallows the key", () => {
    const onClose = vi.fn();
    mount(tree("phone", true, { onClose }));

    escape();
    expect(onClose).not.toHaveBeenCalled();

    escape(dialog());
    expect(onClose).toHaveBeenCalledOnce();
  });
});

describe("Sheet in a column", () => {
  it("columns: an aside in the detail slot", () => {
    mount(tree("columns", true));

    expect(column().parentElement).toBe(detail);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(root).not.toHaveAttribute("inert");

    expect(within(column()).getByText(EYEBROW)).toBeInTheDocument();
    expect(within(column()).getByRole("button", { name: "esc closes" })).toBeInTheDocument();
    expect(within(column()).getByRole("heading", { name: TITLE })).toHaveClass("t-title");
    expect(within(column()).getByText(INTRO)).toBeInTheDocument();
    expect(within(column()).getByText("The thread")).toBeInTheDocument();
  });

  it("columns: drawn from the mock", () => {
    mount(tree("columns", true));

    // Alfred Desktop.dc.html:93: padding 40 24 14, gap 6, a 1 px line under it.
    const header = within(column()).getByRole("heading", { name: TITLE }).parentElement as HTMLElement;
    expect(header).toHaveClass("px-6", "pt-10", "pb-3.5", "gap-1.5");
    expect(header.style.borderBottom).toBe("1px solid var(--line)");

    // :98: padding 18 24 40, scrolling, and the sheet's own gap.
    const body = screen.getByText("The thread").parentElement as HTMLElement;
    expect(body).toHaveClass("px-6", "pt-[18px]", "pb-10", "overflow-y-auto", "gap-3");

    // The mock's type, with the colour moved to one a label can be read at.
    const eyebrow = screen.getByText(EYEBROW);
    expect(eyebrow).toHaveClass("t-label");
    expect(eyebrow).toHaveStyle({ color: "var(--fg2)" });

    const intro = screen.getByText(INTRO);
    expect(intro).toHaveClass("text-[13.5px]", "leading-[1.5]");
    expect(intro).toHaveStyle({ color: "var(--fg2)" });

    // A real button with a 44 px target: 16.5 px of line and 14 px each way.
    const close = button("esc closes");
    expect(close).toHaveClass("t-meta-strong", "relative", "after:absolute", "after:-inset-y-3.5");
  });

  it("columns: with no eyebrow, esc closes stays on the right", () => {
    mount(tree("columns", true, { eyebrow: undefined }));

    expect(screen.queryByText(EYEBROW)).toBeNull();
    expect(button("esc closes")).toHaveClass("ml-auto");
  });

  it("columns: gone the moment open is false", () => {
    const { rerender } = mount(tree("columns", true));
    expect(column()).toBeInTheDocument();

    rerender(tree("columns", false));
    expect(queryColumn()).toBeNull();
    expect(detail).toBeEmptyDOMElement();
  });

  it("columns: Escape anywhere closes, and so does esc closes", () => {
    const onClose = vi.fn();
    mount(tree("columns", true, { onClose }));

    escape();
    expect(onClose).toHaveBeenCalledTimes(1);

    escape(button("Elsewhere"));
    expect(onClose).toHaveBeenCalledTimes(2);

    fireEvent.click(button("esc closes"));
    expect(onClose).toHaveBeenCalledTimes(3);
  });

  it("columns: no other key closes it, and a closed one hears nothing", () => {
    const onClose = vi.fn();
    const { rerender } = mount(tree("columns", true, { onClose }));

    fireEvent.keyDown(document.body, { key: "Enter" });
    expect(onClose).not.toHaveBeenCalled();

    rerender(tree("columns", false, { onClose }));
    escape();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("columns: ignores Escape while #root is inert", () => {
    const onClose = vi.fn();
    mount(tree("columns", true, { onClose }));

    root.setAttribute("inert", "");
    escape();
    expect(onClose).not.toHaveBeenCalled();

    root.removeAttribute("inert");
    escape();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("columns: leaves focus where it was on open", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();

    rerender(tree("columns", true));
    expect(document.activeElement).toBe(button("Opener"));

    // And where it has gone since: closing from outside does not call it back.
    button("Elsewhere").focus();
    rerender(tree("columns", false));
    expect(document.activeElement).toBe(button("Elsewhere"));
  });

  it("columns: returns focus to the opener when closed from inside", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();

    rerender(tree("columns", true));
    button("esc closes").focus();
    expect(document.activeElement).toBe(button("esc closes"));

    rerender(tree("columns", false));
    expect(document.activeElement).toBe(button("Opener"));
  });

  it("columns: re-pointed, it returns focus to the row that last opened it", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();
    rerender(tree("columns", true, { subject: "a" }));

    button("Elsewhere").focus();
    rerender(tree("columns", true, { subject: "b" }));
    button("esc closes").focus();

    rerender(tree("columns", false));
    expect(document.activeElement).toBe(button("Elsewhere"));
  });

  it("columns: a new subject moves nothing, even from <body>", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();
    rerender(tree("columns", true, { subject: "a" }));

    // Safari: pressing a button does not focus it, so the press leaves `<body>` focused.
    button("Opener").blur();
    rerender(tree("columns", true, { subject: "b" }));
    expect(document.activeElement).toBe(document.body);

    // The row that last opened it was never focused, so there is nothing to give back.
    rerender(tree("columns", false));
    expect(document.activeElement).toBe(document.body);
  });

  it("columns: the same subject keeps its opener", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();
    rerender(tree("columns", true, { subject: "a" }));

    button("Elsewhere").focus();
    rerender(tree("columns", true, { subject: "a" }));
    button("esc closes").focus();

    rerender(tree("columns", false));
    expect(document.activeElement).toBe(button("Opener"));
  });

  it("columns: from <body> it cannot tell a reader who was never inside", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();
    rerender(tree("columns", true));

    // A click on blank space, or a press in Safari, leaves `<body>` without entering.
    button("Opener").blur();
    expect(document.activeElement).toBe(document.body);

    rerender(tree("columns", false));
    expect(document.activeElement).toBe(button("Opener"));
  });

  it("columns: a new subject starts the body at the top, and the same one leaves it", () => {
    const { rerender } = mount(tree("columns", true, { subject: "a" }));
    const body = screen.getByText("The thread").parentElement as HTMLElement;
    body.scrollTop = 200;

    rerender(tree("columns", true, { subject: "a" }));
    expect(body.scrollTop).toBe(200);

    rerender(tree("columns", true, { subject: "b" }));
    expect(screen.getByText("The thread").parentElement).toBe(body);
    expect(body.scrollTop).toBe(0);
  });

  it("columns: gives nothing back into a place that has gone inert", () => {
    const { rerender } = mount(tree("columns", false));
    button("Opener").focus();

    rerender(tree("columns", true));
    button("esc closes").focus();
    app.setAttribute("inert", "");

    rerender(tree("columns", false));
    expect(document.activeElement).toBe(document.body);
  });
});

describe("Sheet over the bench", () => {
  it("sheet-over-bench: a dialog in the overBench slot, not modal", () => {
    const onClose = vi.fn();
    mount(tree("sheet-over-bench", true, { onClose }));

    expect(dialog().parentElement).toBe(overBench);
    expect(dialog()).not.toHaveAttribute("aria-modal");
    expect(dialog()).toHaveClass("absolute", "inset-0", "z-20");
    expect(dialog()).not.toHaveClass("viewport-fill");
    expect(root).not.toHaveAttribute("inert");

    // Today's panel, intro first and no eyebrow: only the column draws one.
    expect(screen.getByText(INTRO).nextElementSibling).toBe(screen.getByText("The thread"));
    expect(screen.queryByText(EYEBROW)).toBeNull();

    fireEvent.click(button("Done"));
    expect(onClose).toHaveBeenCalledTimes(1);

    fireEvent.click(button("Close"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("sheet-over-bench: Escape anywhere closes, unless #root is inert", () => {
    const onClose = vi.fn();
    mount(tree("sheet-over-bench", true, { onClose }));

    root.setAttribute("inert", "");
    escape();
    expect(onClose).not.toHaveBeenCalled();

    root.removeAttribute("inert");
    escape(button("Elsewhere"));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("sheet-over-bench: a new subject starts the body at the top, and the same one leaves it", () => {
    const { rerender } = mount(tree("sheet-over-bench", true, { subject: "a" }));
    const body = screen.getByText("The thread").parentElement as HTMLElement;
    body.scrollTop = 200;

    rerender(tree("sheet-over-bench", true, { subject: "a" }));
    expect(body.scrollTop).toBe(200);

    rerender(tree("sheet-over-bench", true, { subject: "b" }));
    expect(screen.getByText("The thread").parentElement).toBe(body);
    expect(body.scrollTop).toBe(0);
  });

  it("sheet-over-bench: a closing sheet leaves from where it was", () => {
    const { rerender } = mount(tree("sheet-over-bench", true, { subject: "a" }));
    const body = screen.getByText("The thread").parentElement as HTMLElement;
    body.scrollTop = 200;

    // `WhySheet` closes by passing no anchor, so the subject goes while the leave runs.
    rerender(tree("sheet-over-bench", false, { subject: null }));
    expect(dialog()).toBeInTheDocument();
    expect(body.scrollTop).toBe(200);
  });

  it("sheet-over-bench: takes focus and gives it back", async () => {
    const { rerender } = mount(tree("sheet-over-bench", false));
    button("Opener").focus();

    rerender(tree("sheet-over-bench", true));
    expect(document.activeElement).toBe(dialog());

    // Stays through the leave, and gives focus back once it has gone.
    rerender(tree("sheet-over-bench", false));
    expect(dialog()).toBeInTheDocument();
    await pastLeave(rerender, tree("sheet-over-bench", false));
    expect(document.activeElement).toBe(button("Opener"));
  });

  it("sheet-over-bench: a reader who has moved on stays where they went", async () => {
    const { rerender } = mount(tree("sheet-over-bench", false));
    button("Opener").focus();

    rerender(tree("sheet-over-bench", true));
    button("Elsewhere").focus();

    rerender(tree("sheet-over-bench", false));
    await pastLeave(rerender, tree("sheet-over-bench", false));
    expect(document.activeElement).toBe(button("Elsewhere"));
  });

  it("sheet-over-bench: a new subject leaves its opener alone", async () => {
    const { rerender } = mount(tree("sheet-over-bench", false));
    button("Opener").focus();
    rerender(tree("sheet-over-bench", true, { subject: "a" }));

    // Focus is in the panel, and the panel is no opener to go back to.
    rerender(tree("sheet-over-bench", true, { subject: "b" }));
    rerender(tree("sheet-over-bench", false));
    await pastLeave(rerender, tree("sheet-over-bench", false));
    expect(document.activeElement).toBe(button("Opener"));
  });

  it("sheet-over-bench: a sheet rising through another's leave keeps the focus it took", async () => {
    const two = (whyOpen: boolean, heldOpen: boolean) =>
      frame(
        "sheet-over-bench",
        { overBench, detail },
        <>
          <Sheet open={whyOpen} title="Why" onClose={() => {}}>
            <p>Thread</p>
          </Sheet>
          <Sheet open={heldOpen} title="Held" onClose={() => {}}>
            <p>Queue</p>
          </Sheet>
        </>,
      );
    const { rerender } = mount(two(false, false));
    button("Opener").focus();
    rerender(two(true, false));

    // The Room is not inert, so its other opener can be pressed with this one up.
    button("Elsewhere").focus();
    rerender(two(false, true));
    const held = screen.getByRole("dialog", { name: "Held" });
    expect(document.activeElement).toBe(held);

    await pastLeave(rerender, two(false, true), "Why");
    expect(document.activeElement).toBe(held);

    rerender(two(false, false));
    await pastLeave(rerender, two(false, false), "Held");
    expect(document.activeElement).toBe(button("Elsewhere"));
  });
});

describe("Sheet in a real Desk", () => {
  // `Desk` portals nothing itself: this is the one place the slots it hands out,
  // the `inert` it sets and a sheet's own checks of `[inert]` are read together.
  function desk(layout: Layout, open: boolean, onClose: () => void = () => {}) {
    return (
      <LayoutContext.Provider value={layout}>
        <Desk
          detailOpen={open}
          room={
            <main>
              <textarea aria-label="Composer" />
              <Sheet open={open} title={TITLE} onClose={onClose}>
                <p>The thread</p>
              </Sheet>
            </main>
          }
          bench={<button type="button">Bench row</button>}
        />
      </LayoutContext.Provider>
    );
  }

  it("sheet-over-bench: quiets the bench, not itself, and still hears Escape", () => {
    const onClose = vi.fn();
    mount(desk("sheet-over-bench", true, onClose));

    expect(dialog().parentElement).toHaveClass("desk-bench");
    expect(button("Bench row").parentElement).toHaveAttribute("inert");
    expect(dialog().closest("[inert]")).toBeNull();

    escape();
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("sheet-over-bench: gives focus back to the bench it covered", async () => {
    const { rerender } = mount(desk("sheet-over-bench", false));
    button("Bench row").focus();

    rerender(desk("sheet-over-bench", true));
    expect(document.activeElement).toBe(dialog());

    rerender(desk("sheet-over-bench", false));
    await pastLeave(rerender, desk("sheet-over-bench", false));
    expect(document.activeElement).toBe(button("Bench row"));
  });

  it("sheet-over-bench: a reader in the composer during the leave stays there", async () => {
    const { rerender } = mount(desk("sheet-over-bench", false));
    button("Bench row").focus();

    rerender(desk("sheet-over-bench", true));
    expect(document.activeElement).toBe(dialog());

    rerender(desk("sheet-over-bench", false));
    expect(dialog()).toBeInTheDocument();
    const composer = screen.getByRole("textbox", { name: "Composer" });
    composer.focus();

    await pastLeave(rerender, desk("sheet-over-bench", false));
    expect(document.activeElement).toBe(composer);
  });

  it("columns: leaves the bench live, with the aside in the third column", () => {
    mount(desk("columns", true));

    expect(column().parentElement).toHaveClass("desk-detail");
    expect(button("Bench row").closest("[inert]")).toBeNull();
  });
});

describe("Sheet with nowhere to go", () => {
  it.each<Layout>(["columns", "sheet-over-bench"])("no slot: falls back to the modal, %s", (layout) => {
    mount(tree(layout, true, {}, { overBench: null, detail: null }));

    expect(dialog()).toHaveAttribute("aria-modal", "true");
    expect(dialog().parentElement).toBe(document.body);
  });

  it("does not take the other layout's slot", () => {
    // Each slot belongs to one layout: `Desk` hides the other with `display: none`.
    const { rerender } = mount(tree("columns", true, {}, { overBench, detail: null }));
    expect(dialog().parentElement).toBe(document.body);

    rerender(tree("sheet-over-bench", true, {}, { overBench: null, detail }));
    expect(dialog().parentElement).toBe(document.body);
  });
});

describe("Sheet across a breakpoint", () => {
  it("moves an open detail, and lets the app go when it leaves the phone", () => {
    const { rerender } = mount(tree("phone", true));
    expect(root).toHaveAttribute("inert");

    rerender(tree("columns", true));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(column().parentElement).toBe(detail);
    expect(root).not.toHaveAttribute("inert");

    rerender(tree("sheet-over-bench", true));
    expect(queryColumn()).toBeNull();
    expect(dialog().parentElement).toBe(overBench);

    rerender(tree("phone", true));
    expect(dialog().parentElement).toBe(document.body);
    expect(root).toHaveAttribute("inert");
  });
});
