import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Composer } from "./Composer";

/** What a tap on the field does. `.focus()` is what fires React's `onFocus`. */
function focusField(): HTMLElement {
  const field = screen.getByLabelText("Message Alfred");
  act(() => field.focus());
  return field;
}

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

  it("keeps the home-indicator inset while the field is idle", () => {
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);
    expect(container.querySelector(".pb-keyboard")!).not.toHaveClass("keyboard-up");
  });

  it("drops the home-indicator inset once the field has focus", () => {
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);

    focusField();

    // The keys cover the indicator strip; paying for it again leaves a band of
    // background between the field and the keyboard.
    expect(container.querySelector(".pb-keyboard")!).toHaveClass("keyboard-up");
  });

  it("takes the inset back when focus leaves", () => {
    const { container } = render(<Composer online onSend={() => {}} hold={null} />);

    const field = focusField();
    act(() => field.blur());

    expect(container.querySelector(".pb-keyboard")!).not.toHaveClass("keyboard-up");
  });

  it("shows the handle under the row while the field is idle", () => {
    const { container } = render(
      <Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />,
    );
    const handle = screen.getByRole("button", { name: "workshop" });
    // Inside the padded element, so the safe-area inset falls below the handle
    // — and *last* inside it, so the handle falls below the row.
    expect(container.querySelector(".pb-keyboard")!.lastElementChild).toContainElement(handle);
  });

  it("hides the handle while the field has focus", () => {
    render(
      <Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />,
    );

    focusField();

    expect(screen.queryByRole("button", { name: "workshop" })).toBeNull();
  });

  it("brings the handle back when focus leaves", () => {
    render(
      <Composer online onSend={() => {}} hold={null} handle={<button type="button">workshop</button>} />,
    );

    const field = focusField();
    act(() => field.blur());

    expect(screen.getByRole("button", { name: "workshop" })).toBeInTheDocument();
  });
});
