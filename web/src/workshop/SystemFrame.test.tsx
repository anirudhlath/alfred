import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SystemRow, SystemSection } from "./SystemFrame";

/**
 * One whole class, not a substring: `toContain("min-h-14")` also passes on
 * `min-h-140` and on a class that merely starts the same way, which is a
 * promise about the frame that a typo could keep.
 */
const hasClass = (element: Element | null | undefined, name: string): boolean =>
  (element?.className ?? "").split(/\s+/).includes(name);


describe("SystemSection", () => {
  it("labels the card and puts the aside beside the label", () => {
    render(
      <SystemSection title="Health" aside={<span>live · 21:14:07</span>}>
        <p>rows</p>
      </SystemSection>,
    );
    const label = screen.getByRole("heading", { name: "Health" });
    expect(label).toHaveClass("t-meta-strong");
    expect(label).toHaveClass("uppercase");
    // `.t-label` is this exact type and carries `--muted`, which is 3.46:1 on
    // the page in light — under AA at 11 px. The label that can be read.
    expect(label).toHaveStyle({ letterSpacing: "0.08em" });
    // A heading's own UA margin would push the card away from its label.
    expect(hasClass(label, "m-0")).toBe(true);
    expect(screen.getByText("live · 21:14:07")).toBeInTheDocument();
  });

  it("draws its children on a card that is a step off the page", () => {
    render(
      <SystemSection title="Quiet">
        <p>rows</p>
      </SystemSection>,
    );
    const card = screen.getByText("rows").parentElement;
    expect(card).toHaveStyle({ background: "var(--surface)" });
    expect(hasClass(card, "rounded-xl")).toBe(true);
    expect(hasClass(card, "border-line")).toBe(true);
  });

  // The 22 px between sections is the label's own top margin rather than a gap
  // on the column, so the first label sits 16 px under the header and every one
  // after it 22 px under the card above. `first:mt-4` is what makes the first
  // one different, and it is one class away from every section being 22.
  it("sits the first section closer to the header than the rest", () => {
    const { container } = render(
      <>
        <SystemSection title="Health">
          <p>one</p>
        </SystemSection>
        <SystemSection title="Quiet">
          <p>two</p>
        </SystemSection>
      </>,
    );
    for (const section of container.querySelectorAll("section")) {
      expect(hasClass(section, "mt-[22px]")).toBe(true);
      expect(hasClass(section, "first:mt-4")).toBe(true);
    }
  });
});

describe("SystemRow", () => {
  it("is tall enough to be pressed and divided from the row above it", () => {
    const { container } = render(
      <SystemSection title="Quiet">
        <SystemRow>
          <span>Do-not-disturb</span>
        </SystemRow>
        <SystemRow>
          <span>Held back</span>
        </SystemRow>
      </SystemSection>,
    );
    const rows = container.querySelectorAll("section > div > div");
    expect(rows).toHaveLength(2);
    for (const row of rows) {
      // 56 px, the handoff's — and what keeps a row whose only control is the
      // row itself over the 44 px minimum.
      expect(hasClass(row, "min-h-14")).toBe(true);
      // The divider is the row's own top border, so a row that only appears in
      // one state takes its line with it.
      expect(hasClass(row, "border-t")).toBe(true);
      expect(hasClass(row, "border-line")).toBe(true);
      expect(hasClass(row, "first:border-t-0")).toBe(true);
    }
  });
});
