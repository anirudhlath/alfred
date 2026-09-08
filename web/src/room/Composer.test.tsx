import { render, screen } from "@testing-library/react";
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

afterEach(() => {
  Object.defineProperty(window, "visualViewport", {
    configurable: true,
    value: originalViewport,
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

  it("sends on Enter", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "Anything tomorrow?{Enter}");

    expect(onSend).toHaveBeenCalledWith("Anything tomorrow?");
  });

  it("refuses to send an empty draft", async () => {
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);

    await user.type(screen.getByPlaceholderText("Ask or tell Alfred"), "   {Enter}");

    expect(onSend).not.toHaveBeenCalled();
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
});
