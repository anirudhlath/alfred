import { useState, type KeyboardEvent, type ReactNode } from "react";
import { useKeyboardOpen } from "@/lib/viewport";

export interface ComposerProps {
  online: boolean;
  onSend: (text: string) => void;
  /** The hold-to-talk button, shown whenever there is no draft. */
  hold: ReactNode;
}

/**
 * 50 px field, 56 px action. Never disabled offline: a message typed while the
 * house is unreachable is queued and retried, and a dead input would hide that.
 */
export function Composer({ online, onSend, hold }: ComposerProps) {
  const [draft, setDraft] = useState("");
  const keyboardOpen = useKeyboardOpen();
  const hasDraft = draft.trim().length > 0;

  function send(): void {
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLInputElement>): void {
    if (event.key === "Enter") {
      event.preventDefault();
      send();
    }
  }

  return (
    // Two elements on purpose: the outer one owns the keyboard and safe-area
    // padding (a Tailwind padding utility on the same element would out-rank the
    // `@layer components` rule and silently drop the inset), the inner one the
    // handoff's own `0 20 8`.
    <div className={`pb-keyboard relative z-[1] ${keyboardOpen ? "keyboard-up" : ""}`}>
      <div className="flex items-center gap-2.5 px-5 pb-2">
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={onKeyDown}
          placeholder={online ? "Ask or tell Alfred" : "Offline · will send when connected"}
          aria-label="Message Alfred"
          autoComplete="off"
          autoCapitalize="sentences"
          autoCorrect="on"
          enterKeyHint="send"
          className="h-[50px] min-w-0 flex-1 rounded-[25px] border px-[18px] text-[15px] outline-none"
          style={{ background: "var(--field)", borderColor: "var(--line)", color: "var(--fg)" }}
        />

        {hasDraft ? (
          <button
            type="button"
            onClick={send}
            aria-label="Send"
            className="flex h-14 w-14 shrink-0 items-center justify-center rounded-[28px] border-0"
            style={{ background: "var(--ink)", color: "var(--paper)" }}
          >
            <span
              aria-hidden="true"
              className="block h-2.5 w-2.5 border-t-2 border-r-2 border-current"
              style={{ transform: "rotate(-45deg) translate(-1px, 1px)" }}
            />
          </button>
        ) : (
          hold
        )}
      </div>
    </div>
  );
}
