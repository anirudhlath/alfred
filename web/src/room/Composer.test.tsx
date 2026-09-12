import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

function setViewport(height: number, innerHeight = 852): void {
  Object.defineProperty(window, "innerHeight", { configurable: true, value: innerHeight });
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: Object.assign(new EventTarget(), { height, offsetTop: 0 }),
  });
}

const originalViewport = window.visualViewport;
const originalInnerHeight = window.innerHeight;

// Both, or the 852 px `innerHeight` outlives the test that set it and every
// later test sees 84 px of phantom keyboard against setup.ts's 768 px viewport.
afterEach(() => {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: originalViewport,
  });
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: originalInnerHeight,
  });
});

describe("Composer", () => {
  it("invites a message and offers the hold slot while empty", () => {
    render(
      <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />,
    );

    expect(screen.getByPlaceholderText("Ask or tell Alfred")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "hold to talk" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("swaps the hold slot for send as soon as there is a draft", async () => {
    const user = userEvent.setup();
    render(
      <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />,
    );

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "hello");

    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "hold to talk" })).toBeNull();
  });

  it("sends on the button and clears the field", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);
    const field = screen.getByPlaceholderText("Ask or tell Alfred");

    await user.type(field, "Turn the hall light off");
    await user.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("Turn the hall light off");
    expect(field).toHaveValue("");
  });

  it("hands focus back to the field after a click on send", async () => {
    const user = userEvent.setup();
    render(
      <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />,
    );
    const field = screen.getByPlaceholderText("Ask or tell Alfred");

    await user.type(field, "Turn the hall light off");
    await user.click(screen.getByRole("button", { name: "Send" }));

    // The button node is patched into the hold slot, not replaced; without the
    // hand-back that is where focus would land.
    expect(field).toHaveFocus();
    expect(screen.getByRole("button", { name: "hold to talk" })).not.toHaveFocus();
  });

  it("sends on Enter", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "Anything tomorrow?{Enter}");

    expect(onSend).toHaveBeenCalledWith("Anything tomorrow?");
  });

  it("trims the draft before it goes out", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "  hello  {Enter}");

    expect(onSend).toHaveBeenCalledWith("hello");
  });

  it("lets Enter confirm a composition instead of sending it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);
    const field = screen.getByPlaceholderText("Ask or tell Alfred");

    await user.type(field, "にほんg");
    fireEvent.keyDown(field, { key: "Enter", isComposing: true });

    expect(onSend).not.toHaveBeenCalled();
    expect(field).toHaveValue("にほんg");
  });

  it("refuses to send an empty draft", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
    // Whitespace is not a draft: the hold slot stays, send never appears.
    expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  });

  it("says what will happen to a message typed offline, and still takes it", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online={false} onSend={onSend} hold={null} />);
    const field = screen.getByPlaceholderText("Offline · will send when connected");

    await user.type(field, "Turn the hall light off{Enter}");

    // Not disabled: the queue is the point. `useRoom` marks it unsent and retries.
    expect(field).toBeEnabled();
    expect(onSend).toHaveBeenCalledWith("Turn the hall light off");
  });

  it("drops the home-indicator inset once the keyboard is up", () => {
    setViewport(500); // 852 - 500 = 352 px of keyboard
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);
    const row = container.querySelector(".pb-keyboard")!;
    expect(row).toHaveClass("keyboard-up");
  });

  it("keeps the inset while the keyboard is down", () => {
    setViewport(852);
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);
    const row = container.querySelector(".pb-keyboard")!;
    expect(row).not.toHaveClass("keyboard-up");
  });

  it("shows the handle under the row while the keyboard is down", () => {
    setViewport(852);
    const { container } = render(
      <Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />,
    );
    const handle = screen.getByRole("button", { name: "workshop" });
    // Inside the padded element, so the safe-area inset falls below the handle
    // — and *last* inside it, so the handle falls below the row.
    expect(container.querySelector(".pb-keyboard")!.lastElementChild).toContainElement(handle);
  });

  it("hides the handle while the keyboard is up", () => {
    setViewport(500);
    render(<Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />);
    expect(screen.queryByRole("button", { name: "workshop" })).toBeNull();
  });
});
