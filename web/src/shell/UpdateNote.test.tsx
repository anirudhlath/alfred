import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OfflineNote } from "@/room/OfflineNote";
import { UpdateNote } from "./UpdateNote";

const SENTENCE = "A newer Alfred is waiting. Nothing is lost; I pick up where we left off.";

/** The note's dot: the one thing in it hidden from assistive tech. */
function dotOf(region: HTMLElement): HTMLElement {
  const dot = region.querySelector<HTMLElement>('[aria-hidden="true"]');
  if (!dot) throw new Error("no dot in the note");
  return dot;
}

describe("UpdateNote", () => {
  it("says what is waiting and what it costs", async () => {
    render(<UpdateNote ready onReload={() => {}} />);
    expect(await screen.findByText(SENTENCE)).toBeInTheDocument();
  });

  it("reloads on the tap", async () => {
    const onReload = vi.fn();
    render(<UpdateNote ready onReload={onReload} />);
    const reload = screen.getByRole("button", { name: "Reload" });
    // A button, never a submit, whatever form the header might one day sit in.
    expect(reload).toHaveAttribute("type", "button");
    await userEvent.click(reload);
    expect(onReload).toHaveBeenCalledTimes(1);
  });

  it("stays mounted and empty while there is nothing to say", () => {
    // Same reason OfflineNote does: VoiceOver can miss a live region that is
    // inserted with its text already in it, and a waiting worker is news.
    const { container } = render(<UpdateNote ready={false} onReload={() => {}} />);
    const region = container.querySelector('[role="status"]');
    expect(region).toBeInTheDocument();
    expect(region).toBeEmptyDOMElement();
    // Out of flow while empty, so the header's gap does not open around nothing.
    expect(region).toHaveClass("sr-only");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("fills the region that was already there, and lets it be seen", () => {
    const { rerender } = render(<UpdateNote ready={false} onReload={() => {}} />);
    const region = screen.getByRole("status");
    rerender(<UpdateNote ready onReload={() => {}} />);
    expect(screen.getByRole("status")).toBe(region);
    expect(region).toHaveTextContent(SENTENCE);
    // jsdom applies no stylesheet, so a note left `sr-only` would pass every
    // other test here while no sighted user ever saw it.
    expect(region).not.toHaveClass("sr-only");
  });

  it("wears the pill of the note it sits under", () => {
    // "The same 13 px surface-pill idiom" as OfflineNote's, held to that note
    // rather than to restated values, so the two cannot drift apart. These are
    // classes, not heights: jsdom lays nothing out. On one line the pill is as
    // tall as its tallest item, and the hit-area test below holds the class
    // that keeps that item the sentence rather than the Reload box.
    render(
      <>
        <OfflineNote online={false} reconnecting={false} lastTrueAt={null} />
        <UpdateNote ready onReload={() => {}} />
      </>,
    );
    const [offline, update] = screen.getAllByRole("status");
    expect(update.className).toBe(offline.className);
    expect(update.style.background).toBe(offline.style.background);
    expect(dotOf(update).className).toBe(dotOf(offline).className);
  });

  it("asks in the accent: the word in --accent-text, the dot in --accent", () => {
    render(<UpdateNote ready onReload={() => {}} />);
    expect(screen.getByRole("button", { name: "Reload" })).toHaveStyle({
      color: "var(--accent-text)",
    });
    expect(dotOf(screen.getByRole("status")).style.background).toBe("var(--accent)");
  });

  it("sets the word apart: a weight above the sentence, at the end of the line", () => {
    // On a phone the sentence wraps and fills its line, so Reload ends up at
    // the pill's end either way; on one line, in a wide window, `ml-auto` is
    // the only thing that puts it there rather than straight after the words.
    render(<UpdateNote ready onReload={() => {}} />);
    expect(screen.getByRole("button", { name: "Reload" })).toHaveClass("font-medium", "ml-auto");
  });

  it("gives the reload a full-size hit area", () => {
    // 13 px text is not a 44 px target. A negative-inset pseudo-element is the
    // house pattern for growing one (EventRow, Switch): a fixed 20 px box, 17 px
    // above it and 7 px below make 44, where the bare 18.2 px line box would make
    // 42.2, and 8 px either side takes the 42 px word to 58. Lopsided on purpose:
    // with no row under the note, the Timeline paints over everything below the
    // pill, so the 7 px stop inside it and the 17 px reach into the gap above.
    // `relative` anchors the inset to the button — without it, the nearest
    // positioned ancestor is the Room's header, and the whole header would tap.
    // `-my-px` keeps the 20 px box from setting the row's height: its margin box
    // is 18 px, under the sentence's 18.2, so a one-line pill stays OfflineNote's
    // 34.2 px, not 36.
    render(<UpdateNote ready onReload={() => {}} />);
    expect(screen.getByRole("button", { name: "Reload" }).className.split(" ")).toEqual(
      expect.arrayContaining([
        "relative",
        "h-5",
        "-my-px",
        "after:absolute",
        "after:-inset-x-2",
        "after:-top-[17px]",
        "after:-bottom-[7px]",
        "after:content-['']",
      ]),
    );
  });
});
