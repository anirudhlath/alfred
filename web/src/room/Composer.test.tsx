import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, onTestFinished, vi } from "vitest";
import { LayoutContext, type Layout } from "@/shell/layout";
import { Composer } from "./Composer";

/** What a tap on the field does. `.focus()` is what fires React's `onFocus`. */
function focusField(): HTMLElement {
  const field = screen.getByLabelText("Message Alfred");
  act(() => field.focus());
  return field;
}

// A draft typed in one test and never sent is kept for the page, which every
// test in this file shares. The real store first: one test replaces it.
afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
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

  it("keeps a half-typed message across a reload", async () => {
    // The update note promises "Nothing is lost; I pick up where we left off."
    // and its Reload is a page load: the field remounts with nothing in React.
    const user = userEvent.setup();
    const { unmount } = render(<Composer online onSend={() => {}} hold={null} />);
    await user.type(screen.getByLabelText("Message Alfred"), "turn off the porch light at");
    expect(sessionStorage.getItem("alfred.draft")).toBe("turn off the porch light at");

    unmount();
    render(<Composer online onSend={() => {}} hold={null} />);

    expect(screen.getByLabelText("Message Alfred")).toHaveValue("turn off the porch light at");
    expect(screen.getByRole("button", { name: "Send" })).toBeInTheDocument();
  });

  it("lets the draft go once it is sent, or emptied by hand", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Composer online onSend={() => {}} hold={null} />);
    const field = screen.getByLabelText("Message Alfred");

    await user.type(field, "Anything tomorrow?{Enter}");
    expect(sessionStorage.getItem("alfred.draft")).toBeNull();

    await user.type(field, "never mind");
    await user.clear(field);
    expect(sessionStorage.getItem("alfred.draft")).toBeNull();

    unmount();
    render(<Composer online onSend={() => {}} hold={null} />);
    expect(screen.getByLabelText("Message Alfred")).toHaveValue("");
  });

  it("drops the older draft when a newer one is refused, so a reload brings back nothing rather than half", async () => {
    // A quota the longer text no longer fits. The refused write would leave the
    // last draft that did fit for the next mount to bring back as if current.
    const store = new Map<string, string>();
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => store.get(key) ?? null,
      setItem: (key: string, value: string) => {
        if (value.length > 12) throw new DOMException("full", "QuotaExceededError");
        store.set(key, value);
      },
      removeItem: (key: string) => void store.delete(key),
    });
    const user = userEvent.setup();
    const { unmount } = render(<Composer online onSend={() => {}} hold={null} />);
    await user.type(screen.getByLabelText("Message Alfred"), "turn off the porch light at nine");
    expect(store.has("alfred.draft")).toBe(false);

    unmount();
    render(<Composer online onSend={() => {}} hold={null} />);

    expect(screen.getByLabelText("Message Alfred")).toHaveValue("");
  });

  it("still takes a message when the page cannot keep one", async () => {
    // Private mode, or a full quota: the draft lives for this mount only. The
    // global itself is replaced — the `sessionStorage` a test sees here is not
    // an instance of the global `Storage`, so a spy on its prototype reaches
    // nothing.
    const refuse = (name: string) => () => {
      throw new DOMException("refused", name);
    };
    vi.stubGlobal("sessionStorage", {
      getItem: refuse("SecurityError"),
      setItem: refuse("QuotaExceededError"),
      removeItem: refuse("SecurityError"),
    });
    const user = userEvent.setup();
    const onSend = vi.fn();
    render(<Composer online onSend={onSend} hold={null} />);
    const field = screen.getByLabelText("Message Alfred");

    await user.type(field, "Turn the hall light off");
    expect(field).toHaveValue("Turn the hall light off");
    await user.keyboard("{Enter}");

    expect(onSend).toHaveBeenCalledWith("Turn the hall light off");
    expect(field).toHaveValue("");
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

  it("sits on the handoff's own row padding for each layout", () => {
    const composer = (layout: Layout) => (
      <LayoutContext.Provider value={layout}>
        <Composer online onSend={() => {}} hold={null} />
      </LayoutContext.Provider>
    );
    const { rerender } = render(composer("phone"));
    const row = screen.getByLabelText("Message Alfred").parentElement?.parentElement as HTMLElement;
    expect(row.classList.contains("pb-2")).toBe(true);

    // §11's `padding: 0 20px 24px`, with no handle under the row to clear.
    rerender(composer("columns"));
    expect(row.classList.contains("pb-6")).toBe(true);
    expect(row.classList.contains("pb-2")).toBe(false);
  });

  describe("the keyboard", () => {
    const composer = (layout: Layout = "columns") => (
      <LayoutContext.Provider value={layout}>
        <Composer online onSend={() => {}} hold={<button type="button">hold to talk</button>} />
      </LayoutContext.Provider>
    );
    const field = () => screen.getByLabelText("Message Alfred");

    it("⌘K and Ctrl+K focus the field", async () => {
      const user = userEvent.setup();
      render(composer());

      await user.keyboard("{Meta>}k{/Meta}");
      expect(field()).toHaveFocus();

      act(() => field().blur());
      await user.keyboard("{Control>}k{/Control}");
      expect(field()).toHaveFocus();
    });

    it.each<Layout>(["sheet-over-bench", "columns"])("works at %s, and claims the key from the browser", (layout) => {
      render(composer(layout));

      expect(fireEvent.keyDown(document.body, { key: "k", metaKey: true })).toBe(false);

      expect(field()).toHaveFocus();
    });

    it("takes focus from another field", () => {
      render(
        <>
          {composer()}
          <input aria-label="A bench's field" />
        </>,
      );
      const other = screen.getByLabelText("A bench's field");
      act(() => other.focus());

      expect(fireEvent.keyDown(other, { key: "k", metaKey: true })).toBe(false);

      expect(field()).toHaveFocus();
    });

    it("still claims the key with the field already focused, or a browser opens its own search", () => {
      render(composer());
      focusField();

      expect(fireEvent.keyDown(field(), { key: "k", ctrlKey: true })).toBe(false);

      expect(field()).toHaveFocus();
    });

    it("takes ⌘K with caps lock on, and leaves ⌘⇧K and ⌘⌥K to the browser", () => {
      render(composer());

      expect(fireEvent.keyDown(document.body, { key: "K", metaKey: true, shiftKey: true })).toBe(true);
      expect(fireEvent.keyDown(document.body, { key: "k", ctrlKey: true, altKey: true })).toBe(true);
      expect(field()).not.toHaveFocus();

      expect(fireEvent.keyDown(document.body, { key: "K", metaKey: true })).toBe(false);
      expect(field()).toHaveFocus();
    });

    it("neither acts inside an inert root", () => {
      render(<div inert>{composer()}</div>);

      expect(fireEvent.keyDown(document.body, { key: "k", metaKey: true })).toBe(true);
      expect(fireEvent.keyDown(document.body, { key: "k", ctrlKey: true })).toBe(true);

      expect(field()).not.toHaveFocus();
    });

    it("names the shortcut for assistive tech", () => {
      render(composer());
      expect(field()).toHaveAttribute("aria-keyshortcuts", "Meta+K Control+K");
    });

    it("shows ⌘K with no draft, hides it with one", async () => {
      const user = userEvent.setup();
      render(composer());

      const hint = screen.getByText("⌘K");
      expect(hint).toHaveAttribute("aria-hidden", "true");
      expect(hint).toHaveClass("t-meta", "pointer-events-none");
      // Positioned against the field's own box, not the row with the hold button in it.
      expect(hint.parentElement).toBe(field().parentElement);
      expect(hint.parentElement).toHaveClass("relative", "min-w-0", "flex-1");
      expect(field()).toHaveClass("pr-11");

      await user.type(field(), "hello");
      expect(screen.queryByText("⌘K")).toBeNull();

      // Whitespace is not a draft, here as for the hold slot.
      await user.clear(field());
      await user.type(field(), "   ");
      expect(screen.getByText("⌘K")).toBeInTheDocument();
    });

    it("Escape blurs the field", async () => {
      const user = userEvent.setup();
      render(composer());

      await user.click(field());
      await user.keyboard("{Escape}");

      expect(field()).not.toHaveFocus();
    });

    it("keeps focus through a composition's Escape", () => {
      render(composer());
      focusField();

      fireEvent.keyDown(field(), { key: "Escape", isComposing: true });

      expect(field()).toHaveFocus();
    });

    describe("beside an open detail", () => {
      /** Stands in for `useEscapeBeside`, on the node it listens on. */
      function detailListening(): ReturnType<typeof vi.fn> {
        const heard = vi.fn();
        const onKeyDown = (event: KeyboardEvent) => {
          if (event.key === "Escape") heard();
        };
        document.addEventListener("keydown", onKeyDown);
        onTestFinished(() => document.removeEventListener("keydown", onKeyDown));
        return heard;
      }

      it("spends the first Escape on the field; the second reaches the detail", async () => {
        const user = userEvent.setup();
        const heard = detailListening();
        render(composer());

        await user.click(field());
        await user.keyboard("{Escape}");
        expect(field()).not.toHaveFocus();
        expect(heard).not.toHaveBeenCalled();

        await user.keyboard("{Escape}");
        expect(heard).toHaveBeenCalledTimes(1);
      });

      it("leaves the phone's Escape going where it goes today", async () => {
        const user = userEvent.setup();
        const heard = detailListening();
        render(composer("phone"));

        await user.click(field());
        await user.keyboard("{Escape}");

        expect(heard).toHaveBeenCalledTimes(1);
      });
    });

    describe("on the phone", () => {
      it("no hint, ⌘K does nothing, Escape keeps focus", async () => {
        const user = userEvent.setup();
        render(composer("phone"));

        expect(screen.queryByText("⌘K")).toBeNull();
        expect(field()).not.toHaveAttribute("aria-keyshortcuts");

        expect(fireEvent.keyDown(document.body, { key: "k", metaKey: true })).toBe(true);
        expect(fireEvent.keyDown(document.body, { key: "k", ctrlKey: true })).toBe(true);
        expect(field()).not.toHaveFocus();

        await user.click(field());
        await user.keyboard("{Escape}");
        expect(field()).toHaveFocus();
      });

      it("keeps its even padding", () => {
        render(composer("phone"));

        expect(field()).toHaveClass("px-[18px]");
        expect(field()).not.toHaveClass("pr-11");
      });
    });

    it("keeps the field and its focus across a breakpoint", () => {
      // A foldable opening mid-sentence: losing this node would close the keyboard.
      const { rerender } = render(composer("phone"));
      const before = focusField();

      rerender(composer("sheet-over-bench"));
      expect(field()).toBe(before);
      expect(before).toHaveFocus();

      rerender(composer("phone"));
      expect(field()).toBe(before);
      expect(before).toHaveFocus();
      act(() => before.blur());

      // And the shortcut went with the width.
      expect(fireEvent.keyDown(document.body, { key: "k", metaKey: true })).toBe(true);
      expect(before).not.toHaveFocus();
    });
  });
});
